import asyncio
import tiktoken
from typing import Literal
from dataclasses import dataclass

@dataclass
class PromptClassification:
    complexity: Literal["simple", "medium", "complex"]
    category: str  # "coding"|"summarization"|"qa"|"analysis"|"creative"|"chat"|"math"|"translation"
    estimated_tokens: int
    recommended_model: str
    recommended_provider: str
    confidence: float
    reasoning: str


class PromptClassifier:
    """
    PromptClassifier analyzes prompts for complexity, category, and token usage,
    suggesting the most optimal cost-effective backend model for the request.
    It operates entirely locally and synchronously, wrapping its sync logic in
    asyncio.to_thread to prevent blocking the async event loop during tokenization.
    """
    async def classify(self, text: str) -> PromptClassification:
        return await asyncio.to_thread(self._classify_sync, text)

    def _classify_sync(self, text: str) -> PromptClassification:
        # 1. Token Estimation via cl100k_base
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            estimated_tokens = len(encoding.encode(text))
        except Exception:
            # Simple fallback counting if tiktoken fails
            estimated_tokens = len(text.split())

        lower_text = text.lower()

        # 2. Category Detection via Keyword Matching
        category = "qa"
        category_keywords = {
            "coding": ["def ", "function", "class ", "import ", "select", "console.log", "bug", "error", "code", "implement", "write a script"],
            "summarization": ["summarize", "tldr", "key points", "brief", "overview", "in short"],
            "math": ["calculate", "solve", "equation", "integral", "derivative", "proof", "formula"],
            "translation": ["translate", "in french", "in spanish", "in hindi", "en español"],
            "analysis": ["analyze", "evaluate", "compare", "pros and cons", "assessment", "review"],
            "creative": ["write a story", "poem", "creative", "imagine", "fictional"],
            "chat": ["hello", "hi", "hey", "how are you", "good morning", "thanks", "thank you"]
        }

        for cat, keywords in category_keywords.items():
            if any(kw in lower_text for kw in keywords):
                category = cat
                break

        # 3. Complexity Classification
        complexity: Literal["simple", "medium", "complex"] = "medium"
        reasoning = "Prompt matches default medium complexity criteria."
        confidence = 0.80

        # Check Complex Rules (First Match Wins)
        if estimated_tokens > 3000:
            complexity = "complex"
            reasoning = f"High token count ({estimated_tokens} tokens > 3000) requires a complex reasoning model."
            confidence = 0.95
        elif any(phrase in lower_text for phrase in ["analyze in depth", "compare and contrast", "write a detailed report", "research"]):
            complexity = "complex"
            reasoning = "Contains explicit analytical phrasing requiring high-level evaluation."
            confidence = 0.90
        elif category == "math" and any(m in lower_text for m in ["equation", "proof", "="]):
            complexity = "complex"
            reasoning = "Mathematical equation or proof request requiring advanced reasoning."
            confidence = 0.85
        elif text.count("?") >= 3:
            complexity = "complex"
            reasoning = f"Prompt contains multiple questions ({text.count('?')}), indicating compound complexity."
            confidence = 0.88

        # Check Simple Rules if not complex
        if complexity == "medium" and estimated_tokens < 200:
            is_simple = False
            if category == "chat":
                is_simple = True
                reasoning = "Casual greeting or conversational chat prompt."
                confidence = 0.90
            elif category == "translation":
                is_simple = True
                reasoning = "Short straightforward translation request."
                confidence = 0.85
            elif category == "qa":
                # Check for technical jargon using full word match
                jargon_terms = {
                    "api", "database", "sql", "kubernetes", "k8s", "docker", "server", "latency",
                    "dockerfile", "alembic", "git", "deployment", "auth", "token", "encryption",
                    "cipher", "vector", "embedding", "model", "prompt", "route", "proxy", "network"
                }
                words = {w.strip("?,.!:;()[]{}*-_") for w in lower_text.split()}
                if not jargon_terms.intersection(words):
                    is_simple = True
                    reasoning = "Short general Q&A prompt without technical domain-specific jargon."
                    confidence = 0.82

            if is_simple:
                complexity = "simple"

        # 4. Model & Provider Recommendations
        if complexity == "simple":
            recommended_model = "claude-haiku-4-5"
            recommended_provider = "anthropic"
        elif complexity == "complex":
            recommended_model = "claude-opus-4-6"
            recommended_provider = "anthropic"
        else:
            recommended_model = "claude-sonnet-4-6"
            recommended_provider = "anthropic"

        return PromptClassification(
            complexity=complexity,
            category=category,
            estimated_tokens=estimated_tokens,
            recommended_model=recommended_model,
            recommended_provider=recommended_provider,
            confidence=confidence,
            reasoning=reasoning
        )
