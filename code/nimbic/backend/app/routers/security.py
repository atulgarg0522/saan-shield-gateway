import math
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.models.security_policy import SecurityPolicy
from app.models.security_violation import SecurityViolation, ViolationTypeEnum, SeverityEnum, ViolationActionEnum
from app.schemas.security import (
    SecurityPolicyResponse,
    SecurityPolicyUpdateRequest,
    PaginatedViolationsResponse,
    SecurityViolationStatsResponse,
    PolicyTestRequest,
    PolicyDecisionResponse,
    CustomPatternSchema,
    CustomPatternCreate,
    CustomPatternValidateRequest,
    CustomPatternValidateResponse,
)
from app.services.security_svc import get_or_create_policy
import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/security", tags=["Security & Compliance Management"])


@router.get("/policy", response_model=SecurityPolicyResponse)
async def get_security_policy(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves the active security policy for the organization.
    """
    policy = await get_or_create_policy(api_key.org_id, db)
    return policy


@router.put("/policy", response_model=SecurityPolicyResponse)
async def update_security_policy(
    payload: SecurityPolicyUpdateRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates the organization's security policy.
    """
    policy = await get_or_create_policy(api_key.org_id, db)
    
    if payload.pii_action is not None:
        policy.pii_action = payload.pii_action
    if payload.code_action is not None:
        policy.code_action = payload.code_action
    if payload.sensitive_action is not None:
        policy.sensitive_action = payload.sensitive_action
    if payload.blocked_regions is not None:
        policy.blocked_regions = payload.blocked_regions
    if payload.allowed_providers_by_region is not None:
        policy.allowed_providers_by_region = payload.allowed_providers_by_region
    if payload.custom_patterns is not None:
        policy.custom_patterns = [p.model_dump() for p in payload.custom_patterns]
    if payload.is_active is not None:
        policy.is_active = payload.is_active

    policy.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    
    # Invalidate Redis policy cache
    try:
        from app.redis import redis_client
        await redis_client.delete(f"policy:org:{api_key.org_id}")
    except Exception as e:
        logger.warn("Failed to clear policy cache", error=str(e))
        
    return policy


@router.get("/violations", response_model=PaginatedViolationsResponse)
async def list_security_violations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    violation_type: Optional[ViolationTypeEnum] = Query(None),
    severity: Optional[SeverityEnum] = Query(None),
    action_taken: Optional[ViolationActionEnum] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns filtered, paginated security violations logs for the organization.
    """
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)

    stmt = select(SecurityViolation).where(SecurityViolation.org_id == api_key.org_id)
    
    if violation_type:
        stmt = stmt.where(SecurityViolation.violation_type == violation_type)
    if severity:
        stmt = stmt.where(SecurityViolation.severity == severity)
    if action_taken:
        stmt = stmt.where(SecurityViolation.action_taken == action_taken)
    if start_date:
        stmt = stmt.where(SecurityViolation.created_at >= start_date)
    if end_date:
        stmt = stmt.where(SecurityViolation.created_at <= end_date)
        
    # Calculate count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total = count_res.scalar() or 0
    
    # Retrieve items
    offset = (page - 1) * limit
    stmt = stmt.order_by(SecurityViolation.created_at.desc()).offset(offset).limit(limit)
    res = await db.execute(stmt)
    items = list(res.scalars().all())
    
    pages = math.ceil(total / limit) if total > 0 else 1
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages
    }


@router.get("/violations/stats", response_model=SecurityViolationStatsResponse)
async def get_security_violation_stats(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Aggregates database metrics for the organization's security violations.
    """
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)

    org_id = api_key.org_id
    
    # 1. Base counts & group by action (to compute total/blocked)
    act_stmt = select(
        SecurityViolation.action_taken,
        func.count(SecurityViolation.id)
    ).where(SecurityViolation.org_id == org_id)
    
    if start_date:
        act_stmt = act_stmt.where(SecurityViolation.created_at >= start_date)
    if end_date:
        act_stmt = act_stmt.where(SecurityViolation.created_at <= end_date)
        
    act_stmt = act_stmt.group_by(SecurityViolation.action_taken)
    act_res = await db.execute(act_stmt)
    act_counts = {str(r[0].value if hasattr(r[0], "value") else r[0]): r[1] for r in act_res.all()}
    
    by_action = {
        "allowed": act_counts.get("allowed", 0),
        "redacted": act_counts.get("redacted", 0),
        "warned": act_counts.get("warned", 0),
        "blocked": act_counts.get("blocked", 0),
    }
    
    total = sum(by_action.values())
    blocked_count = by_action.get("blocked", 0)
    blocked_requests_pct = float((blocked_count / total) * 100) if total > 0 else 0.0
    
    # 2. Group by type
    type_stmt = select(
        SecurityViolation.violation_type,
        func.count(SecurityViolation.id)
    ).where(SecurityViolation.org_id == org_id)
    
    if start_date:
        type_stmt = type_stmt.where(SecurityViolation.created_at >= start_date)
    if end_date:
        type_stmt = type_stmt.where(SecurityViolation.created_at <= end_date)
        
    type_stmt = type_stmt.group_by(SecurityViolation.violation_type)
    type_res = await db.execute(type_stmt)
    type_counts = {str(r[0].value if hasattr(r[0], "value") else r[0]): r[1] for r in type_res.all()}
    
    by_type = {
        "pii": type_counts.get("pii", 0),
        "source_code": type_counts.get("source_code", 0),
        "sensitive": type_counts.get("sensitive_content", 0),
        "residency": type_counts.get("data_residency", 0),
    }
    
    # 3. Group by severity
    sev_stmt = select(
        SecurityViolation.severity,
        func.count(SecurityViolation.id)
    ).where(SecurityViolation.org_id == org_id)
    
    if start_date:
        sev_stmt = sev_stmt.where(SecurityViolation.created_at >= start_date)
    if end_date:
        sev_stmt = sev_stmt.where(SecurityViolation.created_at <= end_date)
        
    sev_stmt = sev_stmt.group_by(SecurityViolation.severity)
    sev_res = await db.execute(sev_stmt)
    sev_counts = {str(r[0].value if hasattr(r[0], "value") else r[0]): r[1] for r in sev_res.all()}
    
    by_severity = {
        "low": sev_counts.get("low", 0),
        "medium": sev_counts.get("medium", 0),
        "high": sev_counts.get("high", 0),
        "critical": sev_counts.get("critical", 0),
    }
    
    # 4. Top violation hours
    is_sqlite = db.bind.dialect.name == "sqlite"
    if is_sqlite:
        hour_expr = func.cast(func.strftime("%H", SecurityViolation.created_at), Integer)
    else:
        hour_expr = func.cast(func.extract("hour", SecurityViolation.created_at), Integer)
        
    hour_stmt = select(
        hour_expr.label("hour_val"),
        func.count(SecurityViolation.id)
    ).where(SecurityViolation.org_id == org_id)
    
    if start_date:
        hour_stmt = hour_stmt.where(SecurityViolation.created_at >= start_date)
    if end_date:
        hour_stmt = hour_stmt.where(SecurityViolation.created_at <= end_date)
        
    hour_stmt = hour_stmt.group_by("hour_val").order_by(func.count(SecurityViolation.id).desc())
    hour_res = await db.execute(hour_stmt)
    top_violation_hours = [{"hour": int(r[0]), "count": r[1]} for r in hour_res.all() if r[0] is not None]
    
    # 5. Trend (group by day)
    if is_sqlite:
        date_expr = func.strftime("%Y-%m-%d", SecurityViolation.created_at)
    else:
        date_expr = func.to_char(SecurityViolation.created_at, "YYYY-MM-DD")
        
    trend_stmt = select(
        date_expr.label("day_val"),
        func.count(SecurityViolation.id)
    ).where(SecurityViolation.org_id == org_id)
    
    if start_date:
        trend_stmt = trend_stmt.where(SecurityViolation.created_at >= start_date)
    if end_date:
        trend_stmt = trend_stmt.where(SecurityViolation.created_at <= end_date)
        
    trend_stmt = trend_stmt.group_by("day_val").order_by("day_val")
    trend_res = await db.execute(trend_stmt)
    trend = [{"date": str(r[0]), "count": r[1]} for r in trend_res.all() if r[0] is not None]
    
    return SecurityViolationStatsResponse(
        total_violations=total,
        by_type=by_type,
        by_severity=by_severity,
        by_action=by_action,
        blocked_requests_pct=blocked_requests_pct,
        top_violation_hours=top_violation_hours,
        trend=trend
    )


@router.post("/policy/test", response_model=PolicyDecisionResponse)
async def test_security_policy(
    payload: PolicyTestRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Evaluates the security policy against a sample prompt in dry-run mode (no logging, no blocking).
    """
    from app.security.policy_engine import PolicyEngine
    from app.redis import redis_client
    
    engine = PolicyEngine()
    decision = await engine.evaluate(
        prompt=payload.prompt,
        request_ip=payload.request_ip,
        provider=payload.provider,
        org_id=api_key.org_id,
        db=db,
        redis=redis_client
    )
    
    # PolicyDecision response serialization helper
    return PolicyDecisionResponse(
        action=decision.action,
        final_prompt=decision.final_prompt,
        violations=[
            {
                "violation_type": v.violation_type,
                "severity": v.severity,
                "details": v.details,
                "action_applied": v.action_applied
            }
            for v in decision.violations
        ],
        should_log_violation=decision.should_log_violation,
        block_reason=decision.block_reason
    )


@router.post("/custom-patterns", response_model=List[CustomPatternSchema])
async def add_custom_pattern(
    payload: CustomPatternCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Appends a new custom regex pattern to the organization's policy.
    """
    policy = await get_or_create_policy(api_key.org_id, db)
    
    patterns = list(policy.custom_patterns or [])
    for p in patterns:
        if p.get("name") == payload.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Custom pattern with name '{payload.name}' already exists."
            )
            
    new_pattern = payload.model_dump()
    new_pattern["regex"] = payload.pattern  # Store under both regex and pattern keys
    
    patterns.append(new_pattern)
    policy.custom_patterns = patterns
    policy.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    
    # Invalidate Redis policy cache
    try:
        from app.redis import redis_client
        await redis_client.delete(f"policy:org:{api_key.org_id}")
    except Exception as e:
        logger.warn("Failed to clear policy cache", error=str(e))
        
    return policy.custom_patterns


@router.delete("/custom-patterns/{pattern_name}", response_model=List[CustomPatternSchema])
async def delete_custom_pattern(
    pattern_name: str,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Removes a custom regex pattern by its name from the organization's policy.
    """
    policy = await get_or_create_policy(api_key.org_id, db)
    
    patterns = list(policy.custom_patterns or [])
    updated_patterns = [p for p in patterns if p.get("name") != pattern_name]
    
    if len(patterns) == len(updated_patterns):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Custom pattern with name '{pattern_name}' not found."
        )
        
    policy.custom_patterns = updated_patterns
    policy.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    
    # Invalidate Redis policy cache
    try:
        from app.redis import redis_client
        await redis_client.delete(f"policy:org:{api_key.org_id}")
    except Exception as e:
        logger.warn("Failed to clear policy cache", error=str(e))
        
    return policy.custom_patterns


@router.post("/custom-patterns/validate", response_model=CustomPatternValidateResponse)
async def validate_custom_pattern(
    payload: CustomPatternValidateRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key)
):
    """
    Validates a regex pattern against a sample test text server-side.
    """
    import re
    try:
        rx = re.compile(payload.pattern)
        raw_matches = rx.findall(payload.test_text)
        matches = []
        for m in raw_matches:
            if isinstance(m, tuple):
                matches.append("".join(str(item) for item in m if item))
            else:
                matches.append(str(m))
        return CustomPatternValidateResponse(
            valid=True,
            matches=matches,
            error=None
        )
    except Exception as e:
        return CustomPatternValidateResponse(
            valid=False,
            matches=[],
            error=str(e)
        )
