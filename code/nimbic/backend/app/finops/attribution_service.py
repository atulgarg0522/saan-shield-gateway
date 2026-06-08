import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import structlog

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finops import Team, Project, Budget, BudgetAlert, BudgetScopeEnum, BudgetPeriodEnum, BudgetAlertTypeEnum
from app.models.request_log import RequestLog

logger = structlog.get_logger()


@dataclass
class Attribution:
    team_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    user_identifier: Optional[str] = None
    department: Optional[str] = None


@dataclass
class BudgetWarning:
    budget_id: uuid.UUID
    scope_type: BudgetScopeEnum
    scope_id: uuid.UUID
    limit_usd: Decimal
    usage_usd: Decimal
    usage_pct: Decimal
    hard_limit: bool = False


@dataclass
class BudgetCheck:
    allowed: bool = True
    hard_blocked: bool = False
    warnings: List[BudgetWarning] = field(default_factory=list)


def _get_key_case_insensitive(data: Optional[Dict[str, Any]], key: str) -> Optional[Any]:
    if not data:
        return None
    for k, v in data.items():
        if k.lower() == key.lower():
            return v
    return None


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(val)
        return True
    except ValueError:
        return False


async def resolve_attribution(org_id: uuid.UUID, headers: Optional[Dict[str, str]], metadata: Optional[Dict[str, Any]], db: AsyncSession) -> Attribution:
    """
    Extracts team, project, user, and department tags from headers/metadata.
    Queries database for teams and projects, auto-creating them if they don't exist.
    """
    team_val = _get_key_case_insensitive(headers, "X-SaaN-Team") or _get_key_case_insensitive(metadata, "x-saan-team") or _get_key_case_insensitive(metadata, "team")
    project_val = _get_key_case_insensitive(headers, "X-SaaN-Project") or _get_key_case_insensitive(metadata, "x-saan-project") or _get_key_case_insensitive(metadata, "project")
    user_val = _get_key_case_insensitive(headers, "X-SaaN-User") or _get_key_case_insensitive(metadata, "x-saan-user") or _get_key_case_insensitive(metadata, "user") or _get_key_case_insensitive(metadata, "user_identifier")
    dept_val = _get_key_case_insensitive(headers, "X-SaaN-Department") or _get_key_case_insensitive(metadata, "x-saan-department") or _get_key_case_insensitive(metadata, "department")

    team_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None

    # 1. Resolve Team
    if team_val:
        team_val = str(team_val).strip()
        if _is_valid_uuid(team_val):
            stmt = select(Team).where(and_(Team.id == uuid.UUID(team_val), Team.org_id == org_id))
            res = await db.execute(stmt)
            team = res.scalar_one_or_none()
        else:
            stmt = select(Team).where(and_(func.lower(Team.name) == team_val.lower(), Team.org_id == org_id))
            res = await db.execute(stmt)
            team = res.scalar_one_or_none()

        if not team:
            # Auto-create team
            team = Team(
                org_id=org_id,
                name=team_val,
                department=str(dept_val).strip() if dept_val else None
            )
            db.add(team)
            await db.commit()
            await db.refresh(team)
        
        team_id = team.id
        if dept_val and team.department != str(dept_val).strip():
            team.department = str(dept_val).strip()
            db.add(team)
            await db.commit()

    # 2. Resolve Project
    if project_val:
        project_val = str(project_val).strip()
        if _is_valid_uuid(project_val):
            stmt = select(Project).where(and_(Project.id == uuid.UUID(project_val), Project.org_id == org_id))
            res = await db.execute(stmt)
            project = res.scalar_one_or_none()
        else:
            stmt = select(Project).where(and_(func.lower(Project.name) == project_val.lower(), Project.org_id == org_id))
            res = await db.execute(stmt)
            project = res.scalar_one_or_none()

        if not project:
            # Auto-create project
            project = Project(
                org_id=org_id,
                team_id=team_id,
                name=project_val
            )
            db.add(project)
            await db.commit()
            await db.refresh(project)

        project_id = project.id
        if team_id and project.team_id != team_id:
            project.team_id = team_id
            db.add(project)
            await db.commit()

    user_identifier = str(user_val).strip() if user_val else None
    department = str(dept_val).strip() if dept_val else None

    # If no team resolved but department was resolved, check if department is a team
    if not team_id and department:
        # Check if a team with name department exists
        stmt = select(Team).where(and_(func.lower(Team.name) == department.lower(), Team.org_id == org_id))
        res = await db.execute(stmt)
        team = res.scalar_one_or_none()
        if not team:
            team = Team(org_id=org_id, name=department, department=department)
            db.add(team)
            await db.commit()
            await db.refresh(team)
        team_id = team.id

    return Attribution(
        team_id=team_id,
        project_id=project_id,
        user_identifier=user_identifier,
        department=department
    )


def _get_period_start(period: BudgetPeriodEnum) -> datetime:
    now = datetime.now(timezone.utc)
    if period == BudgetPeriodEnum.daily:
        return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    elif period == BudgetPeriodEnum.weekly:
        # Monday of current week
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    else:  # monthly
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)


async def _calculate_spend(org_id: uuid.UUID, scope_type: BudgetScopeEnum, scope_id: uuid.UUID, user_identifier: Optional[str], period: BudgetPeriodEnum, db: AsyncSession, redis: Optional[Any]) -> Decimal:
    """
    Computes total cost_usd for the current period from requests_log, using Redis caching.
    """
    cache_key = f"finops:spend:{scope_type.value}:{str(scope_id)}:{period.value}"
    
    if redis:
        try:
            cached_val = await redis.get(cache_key)
            if cached_val is not None:
                return Decimal(cached_val.decode('utf-8') if isinstance(cached_val, bytes) else cached_val)
        except Exception as e:
            logger.warn("Failed to get spend from Redis cache", error=str(e))

    # Query DB
    start_time = _get_period_start(period)
    
    stmt = select(func.sum(RequestLog.cost_usd)).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= start_time,
            RequestLog.status_code == 200
        )
    )

    if scope_type == BudgetScopeEnum.org:
        # Already filtered by org_id
        pass
    elif scope_type == BudgetScopeEnum.team:
        stmt = stmt.where(RequestLog.team_id == scope_id)
    elif scope_type == BudgetScopeEnum.project:
        stmt = stmt.where(RequestLog.project_id == scope_id)
    elif scope_type == BudgetScopeEnum.user:
        if user_identifier:
            stmt = stmt.where(RequestLog.user_identifier == user_identifier)
        else:
            # If no user identifier, return 0 spend
            return Decimal("0.0000")

    res = await db.execute(stmt)
    db_spend = res.scalar() or Decimal("0.0000")
    spend = Decimal(db_spend)

    # Save to Redis
    if redis:
        try:
            await redis.setex(cache_key, 60, str(spend))
        except Exception as e:
            logger.warn("Failed to cache spend in Redis", error=str(e))

    return spend


async def check_budget(
    org_id: uuid.UUID,
    team_id: Optional[uuid.UUID],
    project_id: Optional[uuid.UUID],
    user_identifier: Optional[str],
    db: AsyncSession,
    redis: Optional[Any]
) -> BudgetCheck:
    """
    Checks all applicable budgets (org, team, project, user).
    If any budget exceeds its limit and has hard_limit=True, returns hard_blocked=True.
    """
    # 1. Resolve user UUID (for budget scope queries)
    user_uuid: Optional[uuid.UUID] = None
    if user_identifier:
        user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, user_identifier)

    # 2. Find all active budgets for the org
    stmt = select(Budget).where(Budget.org_id == org_id)
    res = await db.execute(stmt)
    all_budgets = list(res.scalars().all())

    check = BudgetCheck()

    for budget in all_budgets:
        # Check if the budget scope applies to this request
        applies = False
        scope_target_id = budget.scope_id
        
        if budget.scope_type == BudgetScopeEnum.org:
            applies = True
        elif budget.scope_type == BudgetScopeEnum.team and team_id and budget.scope_id == team_id:
            applies = True
        elif budget.scope_type == BudgetScopeEnum.project and project_id and budget.scope_id == project_id:
            applies = True
        elif budget.scope_type == BudgetScopeEnum.user and user_uuid and budget.scope_id == user_uuid:
            applies = True

        if not applies:
            continue

        # Calculate current spend for this budget scope
        spend = await _calculate_spend(
            org_id=org_id,
            scope_type=budget.scope_type,
            scope_id=budget.scope_id,
            user_identifier=user_identifier,
            period=budget.period,
            db=db,
            redis=redis
        )

        limit = budget.limit_usd
        if spend >= limit:
            if budget.hard_limit:
                check.hard_blocked = True
                check.allowed = False
            
            warning = BudgetWarning(
                budget_id=budget.id,
                scope_type=budget.scope_type,
                scope_id=budget.scope_id,
                limit_usd=limit,
                usage_usd=spend,
                usage_pct=(spend / limit * 100) if limit > 0 else Decimal("100.00"),
                hard_limit=budget.hard_limit
            )
            check.warnings.append(warning)
        else:
            alert_threshold = limit * Decimal(budget.alert_pct) / Decimal("100.0")
            if spend >= alert_threshold:
                warning = BudgetWarning(
                    budget_id=budget.id,
                    scope_type=budget.scope_type,
                    scope_id=budget.scope_id,
                    limit_usd=limit,
                    usage_usd=spend,
                    usage_pct=(spend / limit * 100) if limit > 0 else Decimal("0.00"),
                    hard_limit=budget.hard_limit
                )
                check.warnings.append(warning)

    return check


async def dispatch_alerts(
    org_id: uuid.UUID,
    warnings: List[BudgetWarning],
    db: AsyncSession
) -> None:
    """
    Saves budget alert records to the database.
    Deduplicates alerts by checking if an active/unresolved alert of the same type exists.
    """
    for w in warnings:
        alert_type = BudgetAlertTypeEnum.hard_block if w.hard_limit else BudgetAlertTypeEnum.soft_warning

        # Check for unresolved alert of the same type for this budget
        stmt = select(BudgetAlert).where(
            and_(
                BudgetAlert.budget_id == w.budget_id,
                BudgetAlert.alert_type == alert_type,
                BudgetAlert.resolved_at.is_(None)
            )
        )
        res = await db.execute(stmt)
        existing_alert = res.scalar_one_or_none()

        if existing_alert:
            # Update usage stats on the existing alert
            existing_alert.usage_usd = w.usage_usd
            existing_alert.usage_pct = w.usage_pct
            db.add(existing_alert)
        else:
            # Create a new alert
            alert = BudgetAlert(
                budget_id=w.budget_id,
                org_id=org_id,
                alert_type=alert_type,
                usage_pct=w.usage_pct,
                usage_usd=w.usage_usd,
                limit_usd=w.limit_usd
            )
            db.add(alert)
    
    await db.commit()


async def invalidate_spend_cache(
    org_id: uuid.UUID,
    team_id: Optional[uuid.UUID],
    project_id: Optional[uuid.UUID],
    user_identifier: Optional[str],
    redis: Optional[Any]
) -> None:
    """
    Deletes the Redis cache keys for active spend scopes to keep budget checks fresh.
    """
    if not redis:
        return

    periods = ["daily", "weekly", "monthly"]
    keys = []
    for p in periods:
        keys.append(f"finops:spend:org:{str(org_id)}:{p}")
        if team_id:
            keys.append(f"finops:spend:team:{str(team_id)}:{p}")
        if project_id:
            keys.append(f"finops:spend:project:{str(project_id)}:{p}")
        if user_identifier:
            user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, user_identifier)
            keys.append(f"finops:spend:user:{str(user_uuid)}:{p}")

    try:
        await redis.delete(*keys)
    except Exception as e:
        logger.warn("Failed to invalidate spend cache in Redis", error=str(e))
