import uuid
import math
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request_log import RequestLog, ProviderEnum


async def get_paginated_logs(
    org_id: uuid.UUID,
    page: int,
    limit: int,
    provider: Optional[ProviderEnum],
    model: Optional[str],
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    status: Optional[str],
    db: AsyncSession
) -> Tuple[List[RequestLog], int, int]:
    """
    Queries and returns paginated, filtered request trace logs for an organization.
    """
    # Base filter statement
    stmt = select(RequestLog).where(RequestLog.org_id == org_id)

    # Apply filters
    if provider:
        stmt = stmt.where(RequestLog.provider == provider)
    if model:
        stmt = stmt.where(RequestLog.model == model)
    if start_date:
        stmt = stmt.where(RequestLog.created_at >= start_date)
    if end_date:
        stmt = stmt.where(RequestLog.created_at <= end_date)
    if status == "success":
        stmt = stmt.where(RequestLog.status_code >= 200, RequestLog.status_code < 300)
    elif status == "error":
        stmt = stmt.where((RequestLog.status_code >= 400) | (RequestLog.status_code < 200))

    # Calculate total matching entries using subquery count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total = count_res.scalar() or 0

    # Retrieve paginated items ordered by newest first
    offset_val = (page - 1) * limit
    stmt = stmt.order_by(RequestLog.created_at.desc()).offset(offset_val).limit(limit)
    res = await db.execute(stmt)
    items = list(res.scalars().all())

    pages = math.ceil(total / limit) if total > 0 else 1

    return items, total, pages


async def get_aggregated_stats(
    org_id: uuid.UUID,
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    group_by: str,  # "day", "week", "month"
    db: AsyncSession
) -> Dict[str, Any]:
    """
    Aggregates analytical metrics for an organization's gateway transactions.
    Generates total counts, total costs, distributions, and historical time series.
    """
    # 1. Query core KPIs (total requests, total tokens, total costs, latencies, errors)
    kpis_stmt = select(
        func.count(RequestLog.id).label("total_requests"),
        func.sum(RequestLog.total_tokens).label("total_tokens"),
        func.sum(RequestLog.cost_usd).label("total_cost"),
        func.avg(RequestLog.latency_ms).label("avg_latency"),
        func.count(case(((RequestLog.status_code >= 400) | (RequestLog.status_code < 200), 1))).label("total_errors")
    ).where(RequestLog.org_id == org_id)
    
    if start_date:
        kpis_stmt = kpis_stmt.where(RequestLog.created_at >= start_date)
    if end_date:
        kpis_stmt = kpis_stmt.where(RequestLog.created_at <= end_date)

    kpis_res = await db.execute(kpis_stmt)
    kpis = kpis_res.fetchone()

    total_requests = kpis.total_requests or 0 if kpis else 0
    total_tokens = kpis.total_tokens or 0 if kpis else 0
    total_cost = kpis.total_cost or Decimal("0.000000") if kpis else Decimal("0.000000")
    avg_latency = float(kpis.avg_latency or 0.0) if kpis else 0.0
    total_errors = kpis.total_errors or 0 if kpis else 0
    error_rate = float((total_errors / total_requests) * 100) if total_requests > 0 else 0.0

    # 2. Query distribution by provider
    prov_stmt = select(
        RequestLog.provider.label("name"),
        func.count(RequestLog.id).label("count"),
        func.sum(RequestLog.cost_usd).label("cost_usd"),
        func.sum(RequestLog.total_tokens).label("tokens")
    ).where(RequestLog.org_id == org_id)
    
    if start_date:
        prov_stmt = prov_stmt.where(RequestLog.created_at >= start_date)
    if end_date:
        prov_stmt = prov_stmt.where(RequestLog.created_at <= end_date)
        
    prov_stmt = prov_stmt.group_by(RequestLog.provider)
    prov_res = await db.execute(prov_stmt)
    by_provider = [
        {
            "name": str(r.name.value if hasattr(r.name, "value") else r.name),
            "count": r.count,
            "cost_usd": r.cost_usd or Decimal("0.000000"),
            "tokens": r.tokens or 0
        }
        for r in prov_res.fetchall()
    ]

    # 3. Query distribution by model
    model_stmt = select(
        RequestLog.model.label("name"),
        func.count(RequestLog.id).label("count"),
        func.sum(RequestLog.cost_usd).label("cost_usd"),
        func.sum(RequestLog.total_tokens).label("tokens")
    ).where(RequestLog.org_id == org_id)
    
    if start_date:
        model_stmt = model_stmt.where(RequestLog.created_at >= start_date)
    if end_date:
        model_stmt = model_stmt.where(RequestLog.created_at <= end_date)
        
    model_stmt = model_stmt.group_by(RequestLog.model)
    model_res = await db.execute(model_stmt)
    by_model = [
        {
            "name": r.name,
            "count": r.count,
            "cost_usd": r.cost_usd or Decimal("0.000000"),
            "tokens": r.tokens or 0
        }
        for r in model_res.fetchall()
    ]

    # 4. Query historical time series (grouped by day/week/month and provider)
    trunc_field = func.date_trunc(group_by, RequestLog.created_at)
    ts_stmt = select(
        trunc_field.label("interval"),
        RequestLog.provider.label("provider"),
        func.count(RequestLog.id).label("count"),
        func.sum(RequestLog.cost_usd).label("cost_usd")
    ).where(RequestLog.org_id == org_id)
    
    if start_date:
        ts_stmt = ts_stmt.where(RequestLog.created_at >= start_date)
    if end_date:
        ts_stmt = ts_stmt.where(RequestLog.created_at <= end_date)
        
    ts_stmt = ts_stmt.group_by(trunc_field, RequestLog.provider).order_by(trunc_field.asc())

    ts_res = await db.execute(ts_stmt)
    time_series = [
        {
            "time_interval": r.interval.isoformat(),
            "provider": str(r.provider.value if hasattr(r.provider, "value") else r.provider),
            "count": r.count,
            "cost_usd": r.cost_usd or Decimal("0.000000")
        }
        for r in ts_res.fetchall()
    ]

    return {
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "avg_latency_ms": avg_latency,
        "error_rate": error_rate,
        "by_provider": by_provider,
        "by_model": by_model,
        "time_series": time_series
    }
