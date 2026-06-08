import string
import hashlib
import json
import asyncio
import uuid
import numpy as np
from typing import Literal, Optional, List, Dict, Any
from decimal import Decimal
from dataclasses import dataclass
from sqlalchemy import text
from sentence_transformers import SentenceTransformer
import tiktoken

# Module-level singleton for embedding generation
_transformer_model = None

def get_embedding_model() -> SentenceTransformer:
    global _transformer_model
    if _transformer_model is None:
        _transformer_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _transformer_model


@dataclass
class CacheResult:
    hit: bool
    response: Optional[str]
    similarity_score: Optional[float]
    source: Optional[Literal["exact", "semantic", "faq"]]
    cached_model: Optional[str]
    saved_cost_usd: Optional[Decimal]
    low_confidence: bool = False


class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class SemanticCache:
    """
    SemanticCache implements a high-performance three-layer cache architecture:
    1. Exact Match: Redis-based fast exact prompt lookup (<1ms).
    2. Semantic Match: Vector similarity search against historical prompt embeddings using pgvector (<30ms).
    3. FAQ Match: Vector similarity search against predefined organization FAQs using pgvector (<30ms).
    """

    def normalize_prompt(self, prompt: str) -> str:
        """
        Normalizes a prompt by converting it to lowercase, removing punctuation,
        and collapsing multiple spaces into a single space.
        """
        p = prompt.lower()
        p = " ".join(p.split())
        p = p.translate(str.maketrans("", "", string.punctuation))
        return " ".join(p.split())

    def hash_prompt(self, normalized_prompt: str) -> str:
        """
        Generates a SHA256 hex digest of the normalized prompt string.
        """
        return hashlib.sha256(normalized_prompt.encode("utf-8")).hexdigest()

    async def get_embedding(self, text: str) -> List[float]:
        """
        Generates a 384-dimensional vector embedding for the text using the
        all-MiniLM-L6-v2 sentence-transformer model in a non-blocking thread pool.
        """
        model = get_embedding_model()
        embedding_np = await asyncio.to_thread(model.encode, text)
        return embedding_np.tolist()

    def estimate_savings(self, prompt: str) -> Decimal:
        """
        Calculates estimated cost savings of a cache hit using gpt-4o input cost
        as baseline ($0.005 per 1,000 tokens).
        """
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            estimated_tokens = len(encoding.encode(prompt))
        except Exception:
            estimated_tokens = len(prompt.split())
        
        # saved = (estimated_tokens / 1000) * 0.005
        savings = (Decimal(estimated_tokens) / Decimal("1000.0")) * Decimal("0.005")
        return savings.quantize(Decimal("1.000000"))

    async def lookup(self, prompt: str, org_id: str, db, redis) -> CacheResult:
        """
        Look up a prompt in the three-layer cache.
        Returns a CacheResult indicating whether a hit occurred and the cached response.
        """
        try:
            org_id_hex = uuid.UUID(str(org_id)).hex
        except ValueError:
            org_id_hex = str(org_id)
        normalized = self.normalize_prompt(prompt)
        prompt_hash = self.hash_prompt(normalized)

        # --- LAYER 1: Exact Match (Redis) ---
        redis_key = f"cache:{org_id}:{prompt_hash}"
        try:
            cached_val = await redis.get(redis_key)
            if cached_val:
                data = json.loads(cached_val)
                return CacheResult(
                    hit=True,
                    response=data["response"],
                    similarity_score=1.0,
                    source="exact",
                    cached_model=data["model"],
                    saved_cost_usd=self.estimate_savings(prompt),
                    low_confidence=False
                )
        except Exception:
            # Fall through if Redis has connectivity issues to preserve availability
            pass

        # --- LAYER 2: Semantic Match (pgvector - prompt_embeddings) ---
        embedding = await self.get_embedding(prompt)
        embedding_str = str(embedding)

        # pgvector query calculating 1 - cosine_distance
        semantic_query = text("""
            SELECT id, response_text, model_used, 1 - (embedding <=> :emb) AS similarity
            FROM prompt_embeddings
            WHERE org_id = :org_id
            ORDER BY embedding <=> :emb LIMIT 1
        """)
        
        try:
            row = (await db.execute(semantic_query, {"emb": embedding_str, "org_id": org_id_hex})).first()
        except Exception:
            try:
                fallback_query = text("SELECT id, response_text, model_used, embedding FROM prompt_embeddings WHERE org_id = :org_id")
                rows = (await db.execute(fallback_query, {"org_id": org_id_hex})).all()
                row = None
                best_similarity = -1.0
                query_vec = np.array(embedding)
                for r in rows:
                    try:
                        if isinstance(r.embedding, str):
                            cleaned = r.embedding.strip("[]").split(",")
                            emb_vec = np.array([float(x) for x in cleaned])
                        elif isinstance(r.embedding, (list, np.ndarray)):
                            emb_vec = np.array(r.embedding)
                        else:
                            emb_vec = np.array(json.loads(r.embedding))
                        dot_product = np.dot(query_vec, emb_vec)
                        norm_q = np.linalg.norm(query_vec)
                        norm_e = np.linalg.norm(emb_vec)
                        similarity = dot_product / (norm_q * norm_e) if (norm_q > 0 and norm_e > 0) else 0.0
                        if similarity > best_similarity:
                            best_similarity = similarity
                            row = MockRow(
                                id=r.id,
                                response_text=r.response_text,
                                model_used=r.model_used,
                                similarity=similarity
                            )
                    except Exception:
                        continue
            except Exception:
                row = None
        if row:
            similarity = float(row.similarity)
            if similarity >= 0.92:
                # Strong semantic hit
                # Update statistics in background
                update_query = text("""
                    UPDATE prompt_embeddings
                    SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                """)
                await db.execute(update_query, {"id": row.id})
                await db.commit()

                return CacheResult(
                    hit=True,
                    response=row.response_text,
                    similarity_score=similarity,
                    source="semantic",
                    cached_model=row.model_used,
                    saved_cost_usd=self.estimate_savings(prompt),
                    low_confidence=False
                )
            elif 0.80 <= similarity < 0.92:
                # Soft hit (low confidence)
                # Update statistics in background
                update_query = text("""
                    UPDATE prompt_embeddings
                    SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                """)
                await db.execute(update_query, {"id": row.id})
                await db.commit()

                return CacheResult(
                    hit=True,
                    response=row.response_text,
                    similarity_score=similarity,
                    source="semantic",
                    cached_model=row.model_used,
                    saved_cost_usd=self.estimate_savings(prompt),
                    low_confidence=True
                )

        # --- LAYER 3: FAQ Match (pgvector - org_faq_cache) ---
        faq_query = text("""
            SELECT id, answer, 1 - (embedding <=> :emb) AS similarity
            FROM org_faq_cache
            WHERE org_id = :org_id AND is_active = true
            ORDER BY embedding <=> :emb LIMIT 1
        """)

        try:
            faq_row = (await db.execute(faq_query, {"emb": embedding_str, "org_id": org_id_hex})).first()
        except Exception:
            try:
                fallback_faq_query = text("SELECT id, answer, embedding FROM org_faq_cache WHERE org_id = :org_id AND is_active = true")
                faq_rows = (await db.execute(fallback_faq_query, {"org_id": org_id_hex})).all()
                faq_row = None
                best_similarity = -1.0
                query_vec = np.array(embedding)
                for r in faq_rows:
                    try:
                        if isinstance(r.embedding, str):
                            cleaned = r.embedding.strip("[]").split(",")
                            emb_vec = np.array([float(x) for x in cleaned])
                        elif isinstance(r.embedding, (list, np.ndarray)):
                            emb_vec = np.array(r.embedding)
                        else:
                            emb_vec = np.array(json.loads(r.embedding))
                        dot_product = np.dot(query_vec, emb_vec)
                        norm_q = np.linalg.norm(query_vec)
                        norm_e = np.linalg.norm(emb_vec)
                        similarity = dot_product / (norm_q * norm_e) if (norm_q > 0 and norm_e > 0) else 0.0
                        if similarity > best_similarity:
                            best_similarity = similarity
                            faq_row = MockRow(
                                id=r.id,
                                answer=r.answer,
                                similarity=similarity
                            )
                    except Exception:
                        continue
            except Exception:
                faq_row = None
        if faq_row:
            similarity = float(faq_row.similarity)
            if similarity >= 0.88:
                # FAQ hit
                # Update hit counts
                update_faq = text("""
                    UPDATE org_faq_cache
                    SET hit_count = hit_count + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                """)
                await db.execute(update_faq, {"id": faq_row.id})
                await db.commit()

                return CacheResult(
                    hit=True,
                    response=faq_row.answer,
                    similarity_score=similarity,
                    source="faq",
                    cached_model="faq_cache",
                    saved_cost_usd=self.estimate_savings(prompt),
                    low_confidence=False
                )

        # Miss
        return CacheResult(
            hit=False,
            response=None,
            similarity_score=None,
            source=None,
            cached_model=None,
            saved_cost_usd=Decimal("0.000000"),
            low_confidence=False
        )

    async def store(self, prompt: str, response: str, model: str, org_id: str, db, redis, has_pii: bool = False) -> None:
        """
        Store a prompt and its response in both Redis (exact match) and pgvector database (semantic match).
        Enforces filtering constraints (PII check, character length limit).
        """
        # Constrain 1: Don't store responses that contain PII violations
        if has_pii:
            return

        # Constrain 2: Don't store responses longer than 8,000 characters
        if len(response) > 8000:
            return

        normalized = self.normalize_prompt(prompt)
        if not normalized:
            return
            
        prompt_hash = self.hash_prompt(normalized)

        # Store in Redis (Exact match layer, TTL 24 hours)
        redis_key = f"cache:{org_id}:{prompt_hash}"
        try:
            await redis.setex(
                redis_key,
                86400,  # 24 hours TTL
                json.dumps({"response": response, "model": model})
            )
        except Exception:
            # Bypassed if Redis is down, to ensure pgvector store continues
            pass

        # Store in Postgres (Semantic match layer)
        embedding = await self.get_embedding(prompt)
        embedding_str = str(embedding)

        insert_query = text("""
            INSERT INTO prompt_embeddings (id, org_id, prompt_hash, embedding, response_text, model_used)
            VALUES (:id, :org_id, :prompt_hash, :embedding, :response_text, :model_used)
        """)
        
        try:
            org_id_hex = uuid.UUID(str(org_id)).hex
        except ValueError:
            org_id_hex = str(org_id)
        await db.execute(insert_query, {
            "id": uuid.uuid4().hex,
            "org_id": org_id_hex,
            "prompt_hash": prompt_hash,
            "embedding": embedding_str,
            "response_text": response,
            "model_used": model
        })
        await db.commit()

    async def seed_faq(self, org_id: str, faqs: List[Dict[str, Any]], db) -> int:
        """
        Seeds the database with organization FAQs.
        Generates vector embeddings concurrently and bulk inserts them.
        """
        if not faqs:
            return 0

        # Concurrently generate vector embeddings for all questions
        tasks = [self.get_embedding(faq["question"]) for faq in faqs]
        embeddings = await asyncio.gather(*tasks)

        try:
            org_id_hex = uuid.UUID(str(org_id)).hex
        except ValueError:
            org_id_hex = str(org_id)
        inserted_count = 0
        for faq, embedding in zip(faqs, embeddings):
            insert_query = text("""
                INSERT INTO org_faq_cache (id, org_id, question, answer, embedding, category, is_active)
                VALUES (:id, :org_id, :question, :answer, :embedding, :category, true)
            """)
            await db.execute(insert_query, {
                "id": uuid.uuid4().hex,
                "org_id": org_id_hex,
                "question": faq["question"],
                "answer": faq["answer"],
                "embedding": str(embedding),
                "category": faq.get("category")
            })
            inserted_count += 1

        await db.commit()
        return inserted_count
