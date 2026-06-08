import uuid
from decimal import Decimal
from datetime import datetime, timezone, date
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import Organization
from app.models.finops import Team, Project, Budget, BudgetScopeEnum, BudgetPeriodEnum
from app.models.request_log import RequestLog
from app.finops.attribution_service import _get_period_start

# Helper function to get scope name
async def _get_scope_name(org_id: uuid.UUID, scope_type: str, scope_id: Any, db: AsyncSession) -> str:
    if scope_type == "org":
        stmt = select(Organization).where(Organization.id == org_id)
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        return org.name if org else "Organization"
    elif scope_type == "team":
        team_uuid = uuid.UUID(str(scope_id)) if isinstance(scope_id, str) else scope_id
        stmt = select(Team).where(Team.id == team_uuid)
        res = await db.execute(stmt)
        team = res.scalar_one_or_none()
        return team.name if team else "Team"
    elif scope_type == "project":
        project_uuid = uuid.UUID(str(scope_id)) if isinstance(scope_id, str) else scope_id
        stmt = select(Project).where(Project.id == project_uuid)
        res = await db.execute(stmt)
        project = res.scalar_one_or_none()
        return project.name if project else "Project"
    elif scope_type == "user":
        return str(scope_id)
    return "Unknown"


async def get_spend(
    org_id: uuid.UUID,
    scope_type: str,
    scope_id: Any,
    period: str,
    db: AsyncSession
) -> Dict[str, Any]:
    """
    Computes total cost, token consumption, daily cost breakdown, model breakdown,
    and budget usage statistics for the specified scope and period.
    """
    period_enum = BudgetPeriodEnum(period)
    period_start = _get_period_start(period_enum).replace(tzinfo=timezone.utc)
    period_end = datetime.now(timezone.utc)

    # 1. Resolve scope name
    scope_name = await _get_scope_name(org_id, scope_type, scope_id, db)

    # 2. Base query filters
    stmt = select(
        func.sum(RequestLog.cost_usd).label("total_cost"),
        func.count(RequestLog.id).label("total_requests"),
        func.sum(RequestLog.total_tokens).label("total_tokens")
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= period_start,
            RequestLog.status_code == 200
        )
    )

    # Filter by scope
    if scope_type == "team":
        team_uuid = uuid.UUID(str(scope_id)) if isinstance(scope_id, str) else scope_id
        stmt = stmt.where(RequestLog.team_id == team_uuid)
    elif scope_type == "project":
        project_uuid = uuid.UUID(str(scope_id)) if isinstance(scope_id, str) else scope_id
        stmt = stmt.where(RequestLog.project_id == project_uuid)
    elif scope_type == "user":
        stmt = stmt.where(RequestLog.user_identifier == str(scope_id))

    res = await db.execute(stmt)
    kpis = res.fetchone()

    total_cost_usd = Decimal(kpis.total_cost or "0.000000") if kpis else Decimal("0.000000")
    total_requests = kpis.total_requests or 0 if kpis else 0
    total_tokens = kpis.total_tokens or 0 if kpis else 0

    # 3. Fetch budget limit if configured
    budget_limit_usd = None
    usage_pct = None

    scope_query_id = scope_id
    if scope_type == "user":
        scope_query_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(scope_id))
    elif isinstance(scope_id, str):
        try:
            scope_query_id = uuid.UUID(scope_id)
        except ValueError:
            pass

    if scope_query_id:
        budget_stmt = select(Budget).where(
            and_(
                Budget.org_id == org_id,
                Budget.scope_type == BudgetScopeEnum(scope_type),
                Budget.scope_id == scope_query_id,
                Budget.period == period_enum
            )
        )
        budget_res = await db.execute(budget_stmt)
        budget = budget_res.scalar_one_or_none()
        if budget:
            budget_limit_usd = budget.limit_usd
            usage_pct = float((total_cost_usd / budget_limit_usd) * 100) if budget_limit_usd > 0 else 100.0

    # 4. Daily breakdown
    daily_stmt = select(
        func.date(RequestLog.created_at).label("day"),
        func.sum(RequestLog.cost_usd).label("cost_usd"),
        func.count(RequestLog.id).label("requests")
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= period_start,
            RequestLog.status_code == 200
        )
    )

    if scope_type == "team":
        daily_stmt = daily_stmt.where(RequestLog.team_id == team_uuid)
    elif scope_type == "project":
        daily_stmt = daily_stmt.where(RequestLog.project_id == project_uuid)
    elif scope_type == "user":
        daily_stmt = daily_stmt.where(RequestLog.user_identifier == str(scope_id))

    daily_stmt = daily_stmt.group_by(func.date(RequestLog.created_at)).order_by(func.date(RequestLog.created_at).asc())
    daily_res = await db.execute(daily_stmt)

    daily_breakdown = []
    for r in daily_res.fetchall():
        day_val = r.day
        if isinstance(day_val, (datetime, date)):
            day_str = day_val.strftime("%Y-%m-%d")
        else:
            day_str = str(day_val)
        daily_breakdown.append({
            "date": day_str,
            "cost_usd": Decimal(r.cost_usd or "0.000000"),
            "requests": r.requests or 0
        })

    # 5. Model breakdown
    model_stmt = select(
        RequestLog.model.label("model"),
        func.sum(RequestLog.cost_usd).label("cost_usd"),
        func.count(RequestLog.id).label("requests")
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= period_start,
            RequestLog.status_code == 200
        )
    )

    if scope_type == "team":
        model_stmt = model_stmt.where(RequestLog.team_id == team_uuid)
    elif scope_type == "project":
        model_stmt = model_stmt.where(RequestLog.project_id == project_uuid)
    elif scope_type == "user":
        model_stmt = model_stmt.where(RequestLog.user_identifier == str(scope_id))

    model_stmt = model_stmt.group_by(RequestLog.model)
    model_res = await db.execute(model_stmt)

    model_breakdown = []
    for r in model_res.fetchall():
        model_cost = Decimal(r.cost_usd or "0.000000")
        pct = float((model_cost / total_cost_usd) * 100) if total_cost_usd > 0 else 0.0
        model_breakdown.append({
            "model": r.model,
            "cost_usd": model_cost,
            "requests": r.requests or 0,
            "pct": pct
        })

    # 6. Trend calculation
    # Compare average spend of the second half of the days in breakdown vs the first half
    trend = "stable"
    if len(daily_breakdown) >= 2:
        midpoint = len(daily_breakdown) // 2
        first_half = daily_breakdown[:midpoint]
        second_half = daily_breakdown[midpoint:]

        first_half_avg = sum(item["cost_usd"] for item in first_half) / len(first_half)
        second_half_avg = sum(item["cost_usd"] for item in second_half) / len(second_half)

        if second_half_avg > first_half_avg * Decimal("1.05"):
            trend = "increasing"
        elif second_half_avg < first_half_avg * Decimal("0.95"):
            trend = "decreasing"

    return {
        "scope_type": scope_type,
        "scope_id": str(scope_id),
        "scope_name": scope_name,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_cost_usd": total_cost_usd,
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "budget_limit_usd": budget_limit_usd,
        "usage_pct": usage_pct,
        "daily_breakdown": daily_breakdown,
        "model_breakdown": model_breakdown,
        "trend": trend
    }


async def set_budget(
    org_id: uuid.UUID,
    scope_type: str,
    scope_id: Any,
    limit_usd: Decimal,
    period: str,
    alert_pct: int,
    hard_limit: bool,
    db: AsyncSession
) -> Budget:
    """
    Sets or updates a budget limit for a specific scope (org, team, project, or user).
    """
    if limit_usd <= 0:
        raise ValueError("Budget limit must be greater than zero.")

    scope_type_enum = BudgetScopeEnum(scope_type)
    period_enum = BudgetPeriodEnum(period)

    # Determine database scope_id representation
    if scope_type == "user":
        scope_query_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(scope_id))
    else:
        scope_query_id = uuid.UUID(str(scope_id)) if isinstance(scope_id, str) else scope_id

    stmt = select(Budget).where(
        and_(
            Budget.org_id == org_id,
            Budget.scope_type == scope_type_enum,
            Budget.scope_id == scope_query_id,
            Budget.period == period_enum
        )
    )
    res = await db.execute(stmt)
    budget = res.scalar_one_or_none()

    if budget:
        budget.limit_usd = limit_usd
        budget.alert_pct = alert_pct
        budget.hard_limit = hard_limit
    else:
        budget = Budget(
            org_id=org_id,
            scope_type=scope_type_enum,
            scope_id=scope_query_id,
            period=period_enum,
            limit_usd=limit_usd,
            alert_pct=alert_pct,
            hard_limit=hard_limit
        )
        db.add(budget)

    await db.commit()
    await db.refresh(budget)
    return budget


async def get_all_budgets(org_id: uuid.UUID, db: AsyncSession) -> List[Dict[str, Any]]:
    """
    Retrieves all configured budgets with current spend, usage percentage, and status.
    """
    stmt = select(Budget).where(Budget.org_id == org_id)
    res = await db.execute(stmt)
    budgets = res.scalars().all()

    results = []
    for budget in budgets:
        # Calculate current spend for the budget scope and period
        period_start = _get_period_start(budget.period)
        
        spend_stmt = select(func.sum(RequestLog.cost_usd)).where(
            and_(
                RequestLog.org_id == org_id,
                RequestLog.created_at >= period_start,
                RequestLog.status_code == 200
            )
        )

        # Apply specific filters
        if budget.scope_type == BudgetScopeEnum.team:
            spend_stmt = spend_stmt.where(RequestLog.team_id == budget.scope_id)
        elif budget.scope_type == BudgetScopeEnum.project:
            spend_stmt = spend_stmt.where(RequestLog.project_id == budget.scope_id)
        elif budget.scope_type == BudgetScopeEnum.user:
            # Note: For user scope budgets, scope_id stores a uuid5 hash of user_identifier.
            # We must match against the hashed values of log.user_identifier.
            # But in sqlite we can't easily run custom uuid5 on the fly, so we can fetch all logs
            # and resolve them, or query by matching against a subquery or join if needed.
            # Let's write a python resolution or direct uuid mapping.
            # Wait! Since we don't store the hash in RequestLog, we can retrieve all requests log
            # and do python aggregation if there are user budgets, or we can use a query that maps them.
            # Since the number of user identifiers in a period is usually small, we can fetch matching logs.
            # Wait, is there a cleaner way? Yes, we can select request logs where user_identifier is not null
            # and group by user_identifier, then match their uuid5 values in Python!
            # Let's do that!
            pass

        # Handle user scope calculation differently
        if budget.scope_type == BudgetScopeEnum.user:
            # Get all logs for this org and period
            logs_stmt = select(RequestLog.user_identifier, func.sum(RequestLog.cost_usd)).where(
                and_(
                    RequestLog.org_id == org_id,
                    RequestLog.created_at >= period_start,
                    RequestLog.status_code == 200,
                    RequestLog.user_identifier.isnot(None)
                )
            ).group_by(RequestLog.user_identifier)
            logs_res = await db.execute(logs_stmt)
            spend = Decimal("0.000000")
            for user_ident, user_cost in logs_res.fetchall():
                user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, user_ident)
                if user_uuid == budget.scope_id:
                    spend = Decimal(user_cost or "0.000000")
                    break
        else:
            spend_res = await db.execute(spend_stmt)
            spend = Decimal(spend_res.scalar() or "0.000000")

        limit = budget.limit_usd
        usage_pct = float((spend / limit) * 100) if limit > 0 else 100.0

        # Status rules: ok, warning, critical, exceeded
        if usage_pct >= 100.0:
            status = "exceeded" if budget.hard_limit else "critical"
        elif usage_pct >= budget.alert_pct:
            status = "warning"
        else:
            status = "ok"

        # Resolve scope name
        # If it's a user budget, we don't have the cleartext user_identifier in the Budget table.
        # But we can find the matching user_identifier from the logs.
        scope_name = "Organization"
        if budget.scope_type == BudgetScopeEnum.team:
            team_res = await db.execute(select(Team).where(Team.id == budget.scope_id))
            team = team_res.scalar_one_or_none()
            scope_name = team.name if team else "Team"
        elif budget.scope_type == BudgetScopeEnum.project:
            proj_res = await db.execute(select(Project).where(Project.id == budget.scope_id))
            proj = proj_res.scalar_one_or_none()
            scope_name = proj.name if proj else "Project"
        elif budget.scope_type == BudgetScopeEnum.user:
            # Try to lookup user identifier from recent requests log matching this uuid5 hash
            user_stmt = select(RequestLog.user_identifier).where(
                and_(
                    RequestLog.org_id == org_id,
                    RequestLog.user_identifier.isnot(None)
                )
            ).distinct()
            user_res = await db.execute(user_stmt)
            scope_name = "User"
            for row in user_res.scalars().all():
                if uuid.uuid5(uuid.NAMESPACE_DNS, row) == budget.scope_id:
                    scope_name = row
                    break

        results.append({
            "id": str(budget.id),
            "scope_type": budget.scope_type.value,
            "scope_id": str(budget.scope_id),
            "scope_name": scope_name,
            "period": budget.period.value,
            "limit_usd": limit,
            "alert_pct": budget.alert_pct,
            "hard_limit": budget.hard_limit,
            "current_spend_usd": spend,
            "usage_pct": usage_pct,
            "status": status,
            "created_at": budget.created_at.isoformat() if budget.created_at else None,
            "updated_at": budget.updated_at.isoformat() if budget.updated_at else None
        })

    return results
