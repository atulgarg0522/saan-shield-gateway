from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime
from uuid import UUID

class TeamCreate(BaseModel):
    name: str = Field(..., max_length=255)
    department: Optional[str] = Field(None, max_length=255)
    budget_limit_usd: Optional[Decimal] = Field(None, ge=0)
    budget_alert_pct: Optional[int] = Field(80, ge=1, le=100)

class TeamUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    department: Optional[str] = Field(None, max_length=255)
    budget_limit_usd: Optional[Decimal] = Field(None, ge=0)
    budget_alert_pct: Optional[int] = Field(None, ge=1, le=100)

class TeamResponse(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    department: Optional[str]
    budget_limit_usd: Optional[Decimal]
    budget_alert_pct: int
    created_at: datetime
    updated_at: datetime
    current_spend_usd: Optional[Decimal] = None
    usage_pct: Optional[float] = None
    
    class Config:
        from_attributes = True

class ProjectCreate(BaseModel):
    name: str = Field(..., max_length=255)
    team_id: Optional[UUID] = None
    budget_limit_usd: Optional[Decimal] = Field(None, ge=0)
    budget_alert_pct: Optional[int] = Field(80, ge=1, le=100)

class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    team_id: Optional[UUID] = None
    budget_limit_usd: Optional[Decimal] = Field(None, ge=0)
    budget_alert_pct: Optional[int] = Field(None, ge=1, le=100)
    is_active: Optional[bool] = None

class ProjectResponse(BaseModel):
    id: UUID
    org_id: UUID
    team_id: Optional[UUID]
    name: str
    budget_limit_usd: Optional[Decimal]
    budget_alert_pct: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    current_spend_usd: Optional[Decimal] = None
    usage_pct: Optional[float] = None

    class Config:
        from_attributes = True

class BudgetCreate(BaseModel):
    scope_type: str = Field(..., pattern="^(org|team|project|user)$")
    scope_id: str
    period: str = Field(..., pattern="^(daily|weekly|monthly)$")
    limit_usd: Decimal = Field(..., gt=0)
    alert_pct: Optional[int] = Field(80, ge=1, le=100)
    hard_limit: Optional[bool] = Field(False)

class BudgetStatusResponse(BaseModel):
    id: str
    scope_type: str
    scope_id: str
    scope_name: str
    period: str
    limit_usd: Decimal
    alert_pct: int
    hard_limit: bool
    current_spend_usd: Decimal
    usage_pct: float
    status: str
    created_at: Optional[str]
    updated_at: Optional[str]

class DailySpend(BaseModel):
    date: str
    cost_usd: Decimal
    requests: int

class ModelSpend(BaseModel):
    model: str
    cost_usd: Decimal
    requests: int
    pct: float

class SpendSummaryResponse(BaseModel):
    scope_type: str
    scope_id: str
    scope_name: str
    period_start: str
    period_end: str
    total_cost_usd: Decimal
    total_requests: int
    total_tokens: int
    budget_limit_usd: Optional[Decimal]
    usage_pct: Optional[float]
    daily_breakdown: List[DailySpend]
    model_breakdown: List[ModelSpend]
    trend: str

class ForecastResponse(BaseModel):
    current_month_actual_usd: Decimal
    current_month_forecast_usd: Decimal
    next_month_forecast_usd: Decimal
    trend_pct: float
    daily_avg_usd: Decimal
    days_until_budget_exceeded: Optional[int]
    forecast_confidence: float
    model: str

class ModelMixItem(BaseModel):
    model: str
    requests: int
    cost_usd: Decimal
    pct_of_traffic: float

class ScenarioResult(BaseModel):
    name: str
    total_cost_usd: Decimal
    savings_vs_current: Decimal
    savings_pct: float
    feasibility: str

class ModelComparisonResponse(BaseModel):
    current_mix: List[ModelMixItem]
    scenarios: List[ScenarioResult]

class BudgetAlertResponse(BaseModel):
    id: UUID
    budget_id: UUID
    org_id: UUID
    alert_type: str
    usage_pct: Decimal
    usage_usd: Decimal
    limit_usd: Decimal
    resolved_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True

class ChargebackItem(BaseModel):
    group_key: str
    total_cost_usd: Decimal
    total_requests: int
    total_tokens: int
    percentage: float

class ChargebackResponse(BaseModel):
    period: str
    group_by: str
    total_cost_usd: Decimal
    total_requests: int
    total_tokens: int
    items: List[ChargebackItem]
