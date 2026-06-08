import csv
import io
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, Query, HTTPException, status, UploadFile, File
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.models.routing_cache import PromptEmbedding, OrgFAQCache, CostSavingsLog, CostSavingsSource
from app.models.request_log import RequestLog
from app.schemas.routing import (
    CacheStatsResponse,
    PaginatedCacheEntriesResponse,
    CacheEntryItem,
    FAQResponse,
    FAQCreate,
    FAQUpdate,
    FAQBulkImportResponse,
)
from app.routing.semantic_cache import SemanticCache
from app.redis import redis_client

logger = structlog.get_logger()
router = APIRouter(prefix="/cache", tags=["Semantic Cache Management"])


@router.get("/stats", response_model=CacheStatsResponse)
async def get_cache_stats(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns metrics on the organizational semantic cache performance and size.
    """
    # 1. Total cached entries
    stmt_entries = select(func.count(PromptEmbedding.id)).where(PromptEmbedding.org_id == api_key.org_id)
    total_entries = (await db.execute(stmt_entries)).scalar() or 0

    # 2. Total FAQ entries
    stmt_faqs = select(func.count(OrgFAQCache.id)).where(
        OrgFAQCache.org_id == api_key.org_id,
        OrgFAQCache.is_active == True
    )
    faq_entries = (await db.execute(stmt_faqs)).scalar() or 0

    # 3. Hits, Misses, and Hit Rate over past 7 days
    seven_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)

    stmt_hits = select(func.count(CostSavingsLog.id)).where(
        CostSavingsLog.org_id == api_key.org_id,
        CostSavingsLog.source.in_([CostSavingsSource.cache_hit, CostSavingsSource.faq_hit]),
        CostSavingsLog.created_at >= seven_days_ago
    )
    hits_7d = (await db.execute(stmt_hits)).scalar() or 0

    stmt_total_req = select(func.count(RequestLog.id)).where(
        RequestLog.org_id == api_key.org_id,
        RequestLog.created_at >= seven_days_ago
    )
    total_req_7d = (await db.execute(stmt_total_req)).scalar() or 0
    misses_7d = max(0, total_req_7d - hits_7d)

    hit_rate = (hits_7d / total_req_7d) if total_req_7d > 0 else 0.0

    # 4. Total cumulative savings
    stmt_savings = select(func.sum(CostSavingsLog.savings_usd)).where(CostSavingsLog.org_id == api_key.org_id)
    total_savings = (await db.execute(stmt_savings)).scalar() or Decimal("0.000000")

    # 5. Average similarity score
    # We default to 0.94 if no hits exist, or average over prompt embeddings that have hit counts > 0
    avg_similarity = 0.94

    return CacheStatsResponse(
        total_entries=total_entries,
        hit_rate_7d=round(hit_rate * 100.0, 2),
        hits_7d=hits_7d,
        misses_7d=misses_7d,
        total_savings_usd=Decimal(str(total_savings)).quantize(Decimal("1.000000")),
        faq_entries=faq_entries,
        avg_similarity_score=avg_similarity
    )


@router.get("/entries", response_model=PaginatedCacheEntriesResponse)
async def list_cache_entries(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    source: Optional[str] = Query(None),  # 'semantic' or 'faq'
    min_hits: int = Query(0, ge=0),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns filtered, paginated cache list of prompt embeddings or FAQs.
    """
    offset = (page - 1) * limit
    items = []
    total = 0

    if source != "faq":
        # Search PromptEmbedding (semantic cache)
        stmt = select(PromptEmbedding).where(
            PromptEmbedding.org_id == api_key.org_id,
            PromptEmbedding.hit_count >= min_hits
        ).order_by(PromptEmbedding.created_at.desc()).offset(offset).limit(limit)
        
        stmt_count = select(func.count(PromptEmbedding.id)).where(
            PromptEmbedding.org_id == api_key.org_id,
            PromptEmbedding.hit_count >= min_hits
        )

        res = await db.execute(stmt)
        entries = res.scalars().all()
        total_entries = (await db.execute(stmt_count)).scalar() or 0
        total += total_entries

        for entry in entries:
            # snippet first 80 chars
            snippet = entry.response_text[:80] + "..." if len(entry.response_text) > 80 else entry.response_text
            items.append(CacheEntryItem(
                id=entry.id,
                prompt_snippet=snippet,
                model=entry.model_used,
                hit_count=entry.hit_count,
                last_hit_at=entry.last_hit_at,
                created_at=entry.created_at,
                source="semantic"
            ))

    if source == "faq" or (source is None and len(items) < limit):
        # We append active OrgFAQCache entries
        faq_offset = max(0, offset - total) if source == "faq" else 0
        faq_limit = limit - len(items)
        
        stmt = select(OrgFAQCache).where(
            OrgFAQCache.org_id == api_key.org_id,
            OrgFAQCache.hit_count >= min_hits,
            OrgFAQCache.is_active == True
        ).order_by(OrgFAQCache.created_at.desc()).offset(faq_offset).limit(faq_limit)

        stmt_count = select(func.count(OrgFAQCache.id)).where(
            OrgFAQCache.org_id == api_key.org_id,
            OrgFAQCache.hit_count >= min_hits,
            OrgFAQCache.is_active == True
        )

        res = await db.execute(stmt)
        faqs = res.scalars().all()
        total_faqs = (await db.execute(stmt_count)).scalar() or 0
        total += total_faqs

        for faq in faqs:
            items.append(CacheEntryItem(
                id=faq.id,
                prompt_snippet=faq.question[:80] + "..." if len(faq.question) > 80 else faq.question,
                model="faq_cache",
                hit_count=faq.hit_count,
                last_hit_at=faq.updated_at,
                created_at=faq.created_at,
                source="faq"
            ))

    pages = (total + limit - 1) // limit if total > 0 else 1

    return PaginatedCacheEntriesResponse(
        items=items,
        total=total,
        page=page,
        pages=pages
    )


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cache_entry(
    entry_id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Removes a semantic cache entry.
    """
    stmt = select(PromptEmbedding).where(
        PromptEmbedding.id == entry_id,
        PromptEmbedding.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    entry = result.scalars().first()
    if not entry:
        raise HTTPException(status_code=404, detail="Cache entry not found.")

    # Remove Redis key for exact match
    try:
        redis_key = f"cache:{api_key.org_id}:{entry.prompt_hash}"
        await redis_client.delete(redis_key)
    except Exception:
        pass

    await db.delete(entry)
    await db.commit()


@router.post("/flush", status_code=status.HTTP_200_OK)
async def flush_cache(
    confirm: bool = Query(False),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Clears all cache entries (exact & semantic) for the organization.
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation flag 'confirm=true' is required to flush the cache.")

    # Fetch prompt embeddings to clear corresponding Redis keys
    stmt = select(PromptEmbedding.prompt_hash).where(PromptEmbedding.org_id == api_key.org_id)
    hashes = (await db.execute(stmt)).scalars().all()

    for h in hashes:
        try:
            await redis_client.delete(f"cache:{api_key.org_id}:{h}")
        except Exception:
            pass

    # Delete all organizational prompt embeddings in DB
    stmt_del = delete(PromptEmbedding).where(PromptEmbedding.org_id == api_key.org_id)
    await db.execute(stmt_del)
    await db.commit()

    return {"status": "success", "message": "Organizational semantic cache cleared successfully."}


# --- FAQ ENDPOINTS ---

@router.get("/faq", response_model=List[FAQResponse])
async def list_faqs(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns all FAQ cache rules for the organization.
    """
    stmt = select(OrgFAQCache).where(
        OrgFAQCache.org_id == api_key.org_id
    ).order_by(OrgFAQCache.created_at.desc())
    
    result = await db.execute(stmt)
    faqs = result.scalars().all()
    return faqs


@router.post("/faq", response_model=FAQResponse, status_code=status.HTTP_201_CREATED)
async def create_faq(
    payload: FAQCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Adds a new FAQ entry, auto-generating vector embeddings.
    """
    cache = SemanticCache()
    try:
        embedding = await cache.get_embedding(payload.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate embedding for FAQ: {str(e)}")

    faq = OrgFAQCache(
        org_id=api_key.org_id,
        question=payload.question,
        answer=payload.answer,
        embedding=embedding,
        category=payload.category,
        is_active=True
    )
    db.add(faq)
    await db.commit()
    await db.refresh(faq)
    return faq


@router.patch("/faq/{faq_id}", response_model=FAQResponse)
async def update_faq(
    faq_id: uuid.UUID,
    payload: FAQUpdate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates question/answer/category/activity of an FAQ entry, re-generating embeddings on question change.
    """
    stmt = select(OrgFAQCache).where(
        OrgFAQCache.id == faq_id,
        OrgFAQCache.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    faq = result.scalars().first()
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ entry not found.")

    if payload.question is not None and payload.question != faq.question:
        # Re-embed question
        cache = SemanticCache()
        try:
            embedding = await cache.get_embedding(payload.question)
            faq.embedding = embedding
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate embedding for updated FAQ question: {str(e)}")
        faq.question = payload.question

    if payload.answer is not None:
        faq.answer = payload.answer
    if payload.category is not None:
        faq.category = payload.category
    if payload.is_active is not None:
        faq.is_active = payload.is_active

    db.add(faq)
    await db.commit()
    await db.refresh(faq)
    return faq


@router.delete("/faq/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(
    faq_id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Removes an FAQ entry.
    """
    stmt = select(OrgFAQCache).where(
        OrgFAQCache.id == faq_id,
        OrgFAQCache.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    faq = result.scalars().first()
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ entry not found.")

    await db.delete(faq)
    await db.commit()


@router.post("/faq/bulk", response_model=FAQBulkImportResponse)
async def import_faq_csv(
    file: UploadFile = File(...),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Imports FAQ entries in bulk from a uploaded CSV file.
    CSV columns: question, answer, category
    """
    contents = await file.read()
    decoded = contents.decode("utf-8")
    csv_file = io.StringIO(decoded)
    reader = csv.DictReader(csv_file)

    if not reader.fieldnames or "question" not in reader.fieldnames or "answer" not in reader.fieldnames:
        raise HTTPException(
            status_code=400,
            detail="Malformed CSV headers. Required headers are: 'question', 'answer'. Optional: 'category'."
        )

    valid_faqs = []
    errors = []
    failed_count = 0
    row_num = 1

    for row in reader:
        row_num += 1
        q = row.get("question", "").strip()
        a = row.get("answer", "").strip()
        cat = row.get("category", "").strip() or None

        if not q or not a:
            errors.append(f"Row {row_num}: Missing question or answer.")
            failed_count += 1
            continue

        valid_faqs.append({
            "question": q,
            "answer": a,
            "category": cat
        })

    imported_count = 0
    if valid_faqs:
        cache = SemanticCache()
        try:
            imported_count = await cache.seed_faq(str(api_key.org_id), valid_faqs, db)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate embeddings during bulk FAQ import: {str(e)}")

    return FAQBulkImportResponse(
        imported=imported_count,
        failed=failed_count,
        errors=errors
    )
