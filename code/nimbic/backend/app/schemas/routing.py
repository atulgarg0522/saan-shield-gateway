import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# --- ROUTING RULES ---

class RoutingRuleCreate(BaseModel):
    name: str
    conditions: Dict[str, Any] = Field(default_factory=dict)
    target_model: str
    target_provider: str
    priority: int


class RoutingRuleUpdate(BaseModel):
    name: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    target_model: Optional[str] = None
    target_provider: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class RoutingRuleResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    conditions: Dict[str, Any]
    target_model: str
    target_provider: str
    priority: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class RoutingRuleReorderItem(BaseModel):
    id: uuid.UUID
    priority: int


# --- ROUTING DRY-RUN TEST ---

class RoutingTestRequest(BaseModel):
    prompt: str


class RoutingTestResponse(BaseModel):
    complexity: str
    category: str
    estimated_tokens: int
    recommended_model: str
    recommended_provider: str
    confidence: float
    reasoning: str
    routed_model: str
    routed_provider: str
    routing_reason: str
    cache_would_hit: bool
    cache_source: Optional[str] = None
    cache_similarity: Optional[float] = None
    estimated_savings_usd: Decimal
    baseline_cost_usd: Decimal


# --- ROUTING STATS ---

class ModelStatsItem(BaseModel):
    model: str
    request_count: int
    total_cost: Decimal
    avg_latency: float


class ComplexityStatsItem(BaseModel):
    complexity: str
    count: int
    pct: float


class CategoryStatsItem(BaseModel):
    category: str
    count: int


class RuleTriggeredItem(BaseModel):
    rule_name: str
    trigger_count: int


class RoutingStatsResponse(BaseModel):
    by_model: List[ModelStatsItem]
    by_complexity: List[ComplexityStatsItem]
    by_category: List[CategoryStatsItem]
    routing_rules_triggered: List[RuleTriggeredItem]


# --- CACHE STATS ---

class CacheStatsResponse(BaseModel):
    total_entries: int
    hit_rate_7d: float
    hits_7d: int
    misses_7d: int
    total_savings_usd: Decimal
    faq_entries: int
    avg_similarity_score: float


# --- PAGINATED CACHE ENTRIES ---

class CacheEntryItem(BaseModel):
    id: uuid.UUID
    prompt_snippet: str
    model: str
    hit_count: int
    last_hit_at: Optional[datetime] = None
    created_at: datetime
    source: str = "semantic"  # exact/semantic/faq
    similarity: Optional[float] = None


class PaginatedCacheEntriesResponse(BaseModel):
    items: List[CacheEntryItem]
    total: int
    page: int
    pages: int


# --- FAQ ---

class FAQCreate(BaseModel):
    question: str
    answer: str
    category: Optional[str] = None


class FAQUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class FAQResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    question: str
    answer: str
    category: Optional[str]
    hit_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FAQBulkImportResponse(BaseModel):
    imported: int
    failed: int
    errors: List[str]


# --- A/B TESTING ---

class ABTestCreate(BaseModel):
    name: str
    model_a: str
    provider_a: str
    model_b: str
    provider_b: str
    split_pct: Optional[int] = 20
    test_mode: Optional[str] = "traffic_split"
    ends_at: Optional[datetime] = None


class ABTestUpdate(BaseModel):
    status: Optional[str] = None  # "active", "paused", "completed"
    name: Optional[str] = None
    model_a: Optional[str] = None
    provider_a: Optional[str] = None
    model_b: Optional[str] = None
    provider_b: Optional[str] = None
    split_pct: Optional[int] = None
    ends_at: Optional[datetime] = None


class ABTestResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    model_a: str
    provider_a: str
    model_b: str
    provider_b: str
    split_pct: int
    test_mode: str
    status: str
    started_at: datetime
    ends_at: Optional[datetime] = None
    results: Optional[Dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class VariantStats(BaseModel):
    requests: int
    avg_cost: float
    avg_latency: float
    total_cost: float


class ABTestSignificance(BaseModel):
    significant: bool
    p_value: float
    confidence: float
    requests_needed: int
    winner: str
    effect_size: float


class ABTestResultsResponse(BaseModel):
    model_a: VariantStats
    model_b: VariantStats
    winner: str  # "A" | "B" | "inconclusive"
    cost_difference_pct: float
    latency_difference_pct: float
    significance: ABTestSignificance
    mode: str
    shadow_pairs: Optional[int] = None

