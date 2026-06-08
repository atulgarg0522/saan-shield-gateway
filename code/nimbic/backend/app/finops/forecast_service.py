import uuid
import calendar
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finops import Budget, BudgetScopeEnum, BudgetPeriodEnum
from app.models.request_log import RequestLog

async def forecast_spend(
    org_id: uuid.UUID,
    scope_type: str,
    scope_id: Any,
    db: AsyncSession
) -> Dict[str, Any]:
    """
    Retrieves daily spend records for the past 60 days, determines the spend trend,
    selects a projection model (linear 30-day or 7-day moving average), projects EOM
    and next month spend, and estimates the date when budget limit is crossed.
    """
    now = datetime.now(timezone.utc)
    sixty_days_ago = now - timedelta(days=60)

    # 1. Query daily spend for the last 60 days
    stmt = select(
        func.date(RequestLog.created_at).label("day"),
        func.sum(RequestLog.cost_usd).label("cost_usd")
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= sixty_days_ago,
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

    stmt = stmt.group_by(func.date(RequestLog.created_at))
    res = await db.execute(stmt)

    # Fill daily costs in a dictionary
    db_costs = {str(r.day): Decimal(r.cost_usd or "0.000000") for r in res.fetchall()}

    # Reconstruct last 30 and 7 calendar days to compute averages correctly (including $0.0 days)
    last_30_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    last_7_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    spend_30 = sum(db_costs.get(day, Decimal("0.000000")) for day in last_30_days)
    spend_7 = sum(db_costs.get(day, Decimal("0.000000")) for day in last_7_days)

    avg_30day = spend_30 / 30
    avg_7day = spend_7 / 7

    # 2. Select projection model
    # If the 7-day average spend exceeds the 30-day average by > 20%, use 7day_avg (accelerating)
    if avg_7day > avg_30day * Decimal("1.20"):
        model = "7day_avg"
        daily_avg_usd = avg_7day
    else:
        model = "linear"
        daily_avg_usd = avg_30day

    # 3. Calculate current month actual spend
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=timezone.utc)
    month_stmt = select(func.sum(RequestLog.cost_usd)).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= start_of_month,
            RequestLog.status_code == 200
        )
    )
    if scope_type == "team":
        month_stmt = month_stmt.where(RequestLog.team_id == team_uuid)
    elif scope_type == "project":
        month_stmt = month_stmt.where(RequestLog.project_id == project_uuid)
    elif scope_type == "user":
        month_stmt = month_stmt.where(RequestLog.user_identifier == str(scope_id))

    month_res = await db.execute(month_stmt)
    current_month_actual_usd = Decimal(month_res.scalar() or "0.000000")

    # 4. Project end of month spend
    _, num_days = calendar.monthrange(now.year, now.month)
    days_remaining_this_month = num_days - now.day
    current_month_forecast_usd = current_month_actual_usd + (daily_avg_usd * days_remaining_this_month)

    # 5. Project next month spend
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1
    _, next_num_days = calendar.monthrange(next_year, next_month)
    next_month_forecast_usd = daily_avg_usd * next_num_days

    # 6. Calculate trend versus last month
    last_month_start = (now.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=timezone.utc)
    last_month_end = start_of_month
    last_month_stmt = select(func.sum(RequestLog.cost_usd)).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= last_month_start,
            RequestLog.created_at < last_month_end,
            RequestLog.status_code == 200
        )
    )
    if scope_type == "team":
        last_month_stmt = last_month_stmt.where(RequestLog.team_id == team_uuid)
    elif scope_type == "project":
        last_month_stmt = last_month_stmt.where(RequestLog.project_id == project_uuid)
    elif scope_type == "user":
        last_month_stmt = last_month_stmt.where(RequestLog.user_identifier == str(scope_id))

    last_month_res = await db.execute(last_month_stmt)
    last_month_actual_usd = Decimal(last_month_res.scalar() or "0.000000")

    if last_month_actual_usd > 0:
        trend_pct = float(((current_month_forecast_usd - last_month_actual_usd) / last_month_actual_usd) * 100)
    else:
        trend_pct = 0.0

    # 7. Get budget limit and calculate days until exceeded
    days_until_budget_exceeded = None
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
                Budget.period == BudgetPeriodEnum.monthly
            )
        )
        budget_res = await db.execute(budget_stmt)
        budget = budget_res.scalar_one_or_none()
        if budget:
            budget_limit = budget.limit_usd
            budget_remaining = budget_limit - current_month_actual_usd
            if budget_remaining <= 0:
                days_until_budget_exceeded = 0
            elif daily_avg_usd > 0:
                days_until_budget_exceeded = int(budget_remaining / daily_avg_usd)

    # 8. Calculate forecast confidence based on how many days of logs are present
    oldest_stmt = select(func.min(RequestLog.created_at)).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.status_code == 200
        )
    )
    if scope_type == "team":
        oldest_stmt = oldest_stmt.where(RequestLog.team_id == team_uuid)
    elif scope_type == "project":
        oldest_stmt = oldest_stmt.where(RequestLog.project_id == project_uuid)
    elif scope_type == "user":
        oldest_stmt = oldest_stmt.where(RequestLog.user_identifier == str(scope_id))

    oldest_res = await db.execute(oldest_stmt)
    oldest_date = oldest_res.scalar()
    if oldest_date:
        # Check timezone of oldest_date
        if oldest_date.tzinfo is None:
            oldest_date = oldest_date.replace(tzinfo=timezone.utc)
        days_history = (now - oldest_date).days
        if days_history >= 30:
            confidence = 0.85
        elif days_history >= 7:
            confidence = 0.60
        else:
            confidence = 0.40
    else:
        confidence = 0.0

    return {
        "current_month_actual_usd": current_month_actual_usd,
        "current_month_forecast_usd": current_month_forecast_usd,
        "next_month_forecast_usd": next_month_forecast_usd,
        "trend_pct": trend_pct,
        "daily_avg_usd": daily_avg_usd,
        "days_until_budget_exceeded": days_until_budget_exceeded,
        "forecast_confidence": confidence,
        "model": model
    }


async def get_model_comparison(
    org_id: uuid.UUID,
    period_days: int,
    db: AsyncSession
) -> Dict[str, Any]:
    """
    Aggregates LLM usage stats from the last `period_days` (default 30)
    and models scenarios comparison to identify optimization cost savings.
    """
    start_time = datetime.now(timezone.utc) - timedelta(days=period_days)

    # 1. Total request totals in the period
    total_stmt = select(
        func.count(RequestLog.id).label("total_requests"),
        func.sum(RequestLog.prompt_tokens).label("total_prompt"),
        func.sum(RequestLog.completion_tokens).label("total_completion"),
        func.sum(RequestLog.cost_usd).label("total_cost")
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= start_time,
            RequestLog.status_code == 200
        )
    )
    total_res = await db.execute(total_stmt)
    total_kpis = total_res.fetchone()

    total_requests = total_kpis.total_requests or 0 if total_kpis else 0
    total_prompt = total_kpis.total_prompt or 0 if total_kpis else 0
    total_completion = total_kpis.total_completion or 0 if total_kpis else 0
    baseline_cost = Decimal(total_kpis.total_cost or "0.000000") if total_kpis else Decimal("0.000000")

    # 2. Get current model distribution mix
    mix_stmt = select(
        RequestLog.model,
        func.count(RequestLog.id).label("requests"),
        func.sum(RequestLog.cost_usd).label("cost_usd")
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= start_time,
            RequestLog.status_code == 200
        )
    ).group_by(RequestLog.model)
    mix_res = await db.execute(mix_stmt)

    current_mix = []
    for r in mix_res.fetchall():
        requests_count = r.requests or 0
        pct = float((requests_count / total_requests) * 100) if total_requests > 0 else 0.0
        current_mix.append({
            "model": r.model,
            "requests": requests_count,
            "cost_usd": Decimal(r.cost_usd or "0.000000"),
            "pct_of_traffic": pct
        })

    # 3. Simulate Scenarios
    scenarios = []

    # Scenario 1: Current mix (Baseline)
    scenarios.append({
        "name": "Current mix",
        "total_cost_usd": baseline_cost,
        "savings_vs_current": Decimal("0.000000"),
        "savings_pct": 0.0,
        "feasibility": "recommended"
    })

    # Scenario 2: All on cheapest model (gpt-4o-mini)
    # gpt-4o-mini rates: Input $0.000150 / 1k, Output $0.000600 / 1k
    cheapest_cost = (
        (Decimal(total_prompt) * Decimal("0.000150") / Decimal("1000")) +
        (Decimal(total_completion) * Decimal("0.000600") / Decimal("1000"))
    )
    savings_cheapest = baseline_cost - cheapest_cost
    pct_cheapest = float((savings_cheapest / baseline_cost) * 100) if baseline_cost > 0 else 0.0
    scenarios.append({
        "name": "All on cheapest model",
        "total_cost_usd": cheapest_cost,
        "savings_vs_current": savings_cheapest,
        "savings_pct": pct_cheapest,
        "feasibility": "possible"
    })

    # Scenario 3: Optimised routing
    # Map each request to the corresponding tier based on prompt length
    # Simple (<400 prompt tokens): gpt-4o-mini (Input $0.000150/1k, Output $0.000600/1k)
    # Medium (400 to 2000 prompt tokens): gpt-4o (Input $0.005000/1k, Output $0.015000/1k)
    # Complex (>2000 prompt tokens): gpt-4 (Input $0.030000/1k, Output $0.060000/1k)
    reqs_stmt = select(
        RequestLog.prompt_tokens,
        RequestLog.completion_tokens
    ).where(
        and_(
            RequestLog.org_id == org_id,
            RequestLog.created_at >= start_time,
            RequestLog.status_code == 200
        )
    )
    reqs_res = await db.execute(reqs_stmt)
    opt_cost = Decimal("0.000000")
    for r in reqs_res.fetchall():
        prompt = r.prompt_tokens
        completion = r.completion_tokens
        if prompt < 400:
            # simple -> gpt-4o-mini
            opt_cost += (
                (Decimal(prompt) * Decimal("0.000150") / Decimal("1000")) +
                (Decimal(completion) * Decimal("0.000600") / Decimal("1000"))
            )
        elif prompt < 2000:
            # medium -> gpt-4o
            opt_cost += (
                (Decimal(prompt) * Decimal("0.005000") / Decimal("1000")) +
                (Decimal(completion) * Decimal("0.015000") / Decimal("1000"))
            )
        else:
            # complex -> gpt-4
            opt_cost += (
                (Decimal(prompt) * Decimal("0.030000") / Decimal("1000")) +
                (Decimal(completion) * Decimal("0.060000") / Decimal("1000"))
            )

    savings_opt = baseline_cost - opt_cost
    pct_opt = float((savings_opt / baseline_cost) * 100) if baseline_cost > 0 else 0.0
    scenarios.append({
        "name": "Optimised routing",
        "total_cost_usd": opt_cost,
        "savings_vs_current": savings_opt,
        "savings_pct": pct_opt,
        "feasibility": "recommended"
    })

    # Scenario 4: All on GPT-4o
    # gpt-4o rates: Input $0.005000 / 1k, Output $0.015000 / 1k
    gpt4o_cost = (
        (Decimal(total_prompt) * Decimal("0.005000") / Decimal("1000")) +
        (Decimal(total_completion) * Decimal("0.015000") / Decimal("1000"))
    )
    savings_gpt4o = baseline_cost - gpt4o_cost
    pct_gpt4o = float((savings_gpt4o / baseline_cost) * 100) if baseline_cost > 0 else 0.0
    scenarios.append({
        "name": "All on GPT-4o",
        "total_cost_usd": gpt4o_cost,
        "savings_vs_current": savings_gpt4o,
        "savings_pct": pct_gpt4o,
        "feasibility": "possible"
    })

    return {
        "current_mix": current_mix,
        "scenarios": scenarios
    }
