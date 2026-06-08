from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.models.request_log import ProviderEnum
from app.schemas.log import PaginatedLogsResponse, StatsResponse
from app.services import analytics_svc

router = APIRouter(prefix="/logs", tags=["Telemetry & Analytics Dashboard"])


@router.get("", response_model=PaginatedLogsResponse)
async def list_request_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    provider: Optional[ProviderEnum] = Query(None),
    model: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    status: Optional[str] = Query(None, regex="^(success|error)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns filtered, paginated execution trace logs for the organization.
    """
    items, total, pages = await analytics_svc.get_paginated_logs(
        org_id=api_key.org_id,
        page=page,
        limit=limit,
        provider=provider,
        model=model,
        start_date=start_date,
        end_date=end_date,
        status=status,
        db=db
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages
    }


@router.get("/stats", response_model=StatsResponse)
async def get_analytics_statistics(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    group_by: str = Query("day", regex="^(day|week|month)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns aggregated KPI statistics and time-series reports grouped by day, week, or month.
    """
    try:
        stats = await analytics_svc.get_aggregated_stats(
            org_id=api_key.org_id,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
            db=db
        )
        return stats
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to aggregate analytics database telemetry: {str(e)}"
        )
