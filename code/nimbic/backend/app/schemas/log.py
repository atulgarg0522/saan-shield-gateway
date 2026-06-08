from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict
from app.models.request_log import ProviderEnum


class LogItemResponse(BaseModel):
    """
    Detailed proxy transaction record item.
    """
    id: uuid.UUID
    provider: ProviderEnum
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Decimal
    latency_ms: int
    status_code: int
    created_at: datetime
    user_identifier: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedLogsResponse(BaseModel):
    """
    Enforces standard pagination wrapping for trace lists.
    """
    items: List[LogItemResponse]
    total: int
    page: int
    pages: int


class TimeSeriesPoint(BaseModel):
    """
    A single point representing aggregated performance metrics over a time slice.
    """
    time_interval: str
    count: int
    cost_usd: Decimal
    provider: Optional[str] = None


class GroupedMetric(BaseModel):
    """
    Metric aggregates grouped by a criteria (provider or model).
    """
    name: str
    count: int
    cost_usd: Decimal
    tokens: int


class StatsResponse(BaseModel):
    """
    Unified analytics dashboard payload carrying key performance metrics,
    provider distributions, model distributions, and historical time series.
    """
    total_requests: int
    total_tokens: int
    total_cost_usd: Decimal
    avg_latency_ms: float
    error_rate: float
    by_provider: List[GroupedMetric]
    by_model: List[GroupedMetric]
    time_series: List[TimeSeriesPoint]
