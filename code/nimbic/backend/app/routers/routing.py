import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.models.routing_cache import RoutingRule
from app.models.request_log import RequestLog
from app.schemas.routing import (
    RoutingRuleResponse,
    RoutingRuleCreate,
    RoutingRuleUpdate,
    RoutingRuleReorderItem,
    RoutingTestRequest,
    RoutingTestResponse,
    RoutingStatsResponse,
    ModelStatsItem,
    ComplexityStatsItem,
    CategoryStatsItem,
    RuleTriggeredItem,
)
from app.routing.prompt_classifier import PromptClassifier
from app.routing.smart_router import SmartRouter
from app.routing.semantic_cache import SemanticCache
from app.redis import redis_client

logger = structlog.get_logger()
router = APIRouter(prefix="/routing", tags=["Smart Routing Management"])


@router.get("/rules", response_model=List[RoutingRuleResponse])
async def list_routing_rules(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns all routing rules for the organization sorted by priority ascending.
    """
    stmt = select(RoutingRule).where(
        RoutingRule.org_id == api_key.org_id
    ).order_by(RoutingRule.priority.asc())
    
    result = await db.execute(stmt)
    rules = result.scalars().all()
    return rules


@router.post("/rules", response_model=RoutingRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_routing_rule(
    payload: RoutingRuleCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new routing rule for the organization.
    """
    # Invalidate cached rules for organization
    try:
        await redis_client.delete(f"routing_rules:{api_key.org_id}")
    except Exception:
        pass

    rule = RoutingRule(
        org_id=api_key.org_id,
        name=payload.name,
        conditions=payload.conditions,
        target_model=payload.target_model,
        target_provider=payload.target_provider,
        priority=payload.priority,
        is_active=True
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}", response_model=RoutingRuleResponse)
async def update_routing_rule(
    rule_id: uuid.UUID,
    payload: RoutingRuleUpdate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates specific fields of an existing routing rule.
    """
    stmt = select(RoutingRule).where(
        RoutingRule.id == rule_id,
        RoutingRule.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    rule = result.scalars().first()
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found.")

    # Invalidate cached rules
    try:
        await redis_client.delete(f"routing_rules:{api_key.org_id}")
    except Exception:
        pass

    if payload.name is not None:
        rule.name = payload.name
    if payload.conditions is not None:
        rule.conditions = payload.conditions
    if payload.target_model is not None:
        rule.target_model = payload.target_model
    if payload.target_provider is not None:
        rule.target_provider = payload.target_provider
    if payload.priority is not None:
        rule.priority = payload.priority
    if payload.is_active is not None:
        rule.is_active = payload.is_active

    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_rule(
    rule_id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Deletes a routing rule.
    """
    stmt = select(RoutingRule).where(
        RoutingRule.id == rule_id,
        RoutingRule.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    rule = result.scalars().first()
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found.")

    # Invalidate cached rules
    try:
        await redis_client.delete(f"routing_rules:{api_key.org_id}")
    except Exception:
        pass

    await db.delete(rule)
    await db.commit()


@router.post("/rules/reorder", status_code=status.HTTP_200_OK)
async def reorder_routing_rules(
    payload: List[RoutingRuleReorderItem],
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Reorders rules priority sequence.
    """
    # Invalidate cached rules
    try:
        await redis_client.delete(f"routing_rules:{api_key.org_id}")
    except Exception:
        pass

    for item in payload:
        stmt = select(RoutingRule).where(
            RoutingRule.id == item.id,
            RoutingRule.org_id == api_key.org_id
        )
        result = await db.execute(stmt)
        rule = result.scalars().first()
        if rule:
            rule.priority = item.priority
            db.add(rule)
            
    await db.commit()
    return {"status": "success", "message": "Rules reordered successfully."}


@router.post("/test", response_model=RoutingTestResponse)
async def test_routing_simulation(
    payload: RoutingTestRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates classifying and routing a prompt without making outbound calls.
    Also queries the semantic cache engine to verify if a cache hit would occur.
    """
    classifier = PromptClassifier()
    smart_router = SmartRouter()
    semantic_cache = SemanticCache()

    # 1. Classify
    classification = await classifier.classify(payload.prompt)
    
    # 2. Route
    route = await smart_router.route(classification, str(api_key.org_id), db, redis_client)

    # 3. Simulate Cache lookup
    cache_result = await semantic_cache.lookup(payload.prompt, str(api_key.org_id), db, redis_client)

    return RoutingTestResponse(
        complexity=classification.complexity,
        category=classification.category,
        estimated_tokens=classification.estimated_tokens,
        recommended_model=classification.recommended_model,
        recommended_provider=classification.recommended_provider,
        confidence=classification.confidence,
        reasoning=classification.reasoning,
        routed_model=route.model,
        routed_provider=route.provider,
        routing_reason=route.routing_reason,
        cache_would_hit=cache_result.hit,
        cache_source=cache_result.source,
        cache_similarity=cache_result.similarity_score,
        estimated_savings_usd=cache_result.saved_cost_usd if cache_result.hit else Decimal("0.000000"),
        baseline_cost_usd=route.baseline_cost_usd
    )


@router.get("/stats", response_model=RoutingStatsResponse)
async def get_routing_stats(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Aggregates routing analytics logs.
    """
    # Parse and normalize timezone bounds
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)

    # Default to past 30 days if not set
    if not start_date:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    if not end_date:
        end_date = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Grouped by model stats
    # Select: model, count(id), sum(cost_usd), avg(latency_ms)
    from sqlalchemy import func
    stmt_model = select(
        RequestLog.model,
        func.count(RequestLog.id).label("request_count"),
        func.sum(RequestLog.cost_usd).label("total_cost"),
        func.avg(RequestLog.latency_ms).label("avg_latency")
    ).where(
        RequestLog.org_id == api_key.org_id,
        RequestLog.created_at >= start_date,
        RequestLog.created_at <= end_date
    ).group_by(RequestLog.model)

    res_model = await db.execute(stmt_model)
    model_rows = res_model.all()

    by_model = []
    total_requests = 0
    complexity_counts = {"simple": 0, "medium": 0, "complex": 0}

    for row in model_rows:
        model_name = row.model
        req_count = row.request_count
        total_requests += req_count
        
        # Determine complexity based on model naming patterns
        model_lower = model_name.lower()
        if "haiku" in model_lower or "mini" in model_lower or "flash" in model_lower:
            complexity = "simple"
        elif "opus" in model_lower or "gpt-4o" in model_lower or "turbo" in model_lower or "pro" in model_lower:
            complexity = "complex"
        else:
            complexity = "medium"
            
        complexity_counts[complexity] += req_count

        by_model.append(ModelStatsItem(
            model=model_name,
            request_count=req_count,
            total_cost=Decimal(str(row.total_cost or 0)).quantize(Decimal("1.000000")),
            avg_latency=float(row.avg_latency or 0)
        ))

    # 2. Complexity percentages
    by_complexity = []
    for comp, count in complexity_counts.items():
        pct = (count / total_requests * 100.0) if total_requests > 0 else 0.0
        by_complexity.append(ComplexityStatsItem(
            complexity=comp,
            count=count,
            pct=round(pct, 2)
        ))

    # 3. Category & Triggered Rules breakdown
    category_counts = {}
    rule_counts = {}

    stmt_all_logs = select(RequestLog).where(
        RequestLog.org_id == api_key.org_id,
        RequestLog.created_at >= start_date,
        RequestLog.created_at <= end_date
    )
    res_logs = await db.execute(stmt_all_logs)
    all_logs = res_logs.scalars().all()

    for log in all_logs:
        meta = log.request_metadata or {}
        cat = meta.get("category", "chat")
        category_counts[cat] = category_counts.get(cat, 0) + 1
        
        reason = meta.get("routing_reason")
        if reason and "org rule match:" in reason:
            rule_name = reason.replace("org rule match:", "").strip()
            rule_counts[rule_name] = rule_counts.get(rule_name, 0) + 1

    by_category = [CategoryStatsItem(category=c, count=n) for c, n in category_counts.items()]
    routing_rules_triggered = [RuleTriggeredItem(rule_name=r, trigger_count=n) for r, n in rule_counts.items()]

    return RoutingStatsResponse(
        by_model=by_model,
        by_complexity=by_complexity,
        by_category=by_category,
        routing_rules_triggered=routing_rules_triggered
    )


# --- A/B TESTING ENDPOINTS ---

from app.models.ab_test import ABTest, ABTestResult
from app.schemas.routing import (
    ABTestCreate,
    ABTestUpdate,
    ABTestResponse,
    VariantStats,
    ABTestResultsResponse
)

@router.post("/ab-tests", response_model=ABTestResponse, status_code=status.HTTP_201_CREATED)
async def create_ab_test(
    payload: ABTestCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new A/B test for the organization.
    Only one test can be active at a time.
    """
    # Check if there is already an active test
    active_stmt = select(ABTest).where(
        ABTest.org_id == api_key.org_id,
        ABTest.status == "active"
    )
    active_res = await db.execute(active_stmt)
    if active_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An active A/B test already exists for this organization. Complete or pause it first."
        )

    # Invalidate Redis cache
    try:
        await redis_client.delete(f"ab_test:active:{api_key.org_id}")
    except Exception:
        pass

    test = ABTest(
        org_id=api_key.org_id,
        name=payload.name,
        model_a=payload.model_a,
        provider_a=payload.provider_a,
        model_b=payload.model_b,
        provider_b=payload.provider_b,
        split_pct=payload.split_pct if payload.split_pct is not None else 20,
        test_mode=payload.test_mode if payload.test_mode is not None else "traffic_split",
        status="active",
        started_at=datetime.now(timezone.utc),
        ends_at=payload.ends_at
    )
    db.add(test)
    await db.commit()
    await db.refresh(test)
    return test


@router.get("/ab-tests", response_model=List[ABTestResponse])
async def list_ab_tests(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists all A/B tests for the organization.
    """
    stmt = select(ABTest).where(
        ABTest.org_id == api_key.org_id
    ).order_by(ABTest.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/ab-tests/{test_id}", response_model=ABTestResponse)
async def update_ab_test(
    test_id: uuid.UUID,
    payload: ABTestUpdate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates or changes status (pause/resume/complete) of an A/B test.
    """
    stmt = select(ABTest).where(
        ABTest.id == test_id,
        ABTest.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    test = result.scalars().first()
    if not test:
        raise HTTPException(status_code=404, detail="A/B test not found.")

    # Invalidate Redis cache
    try:
        await redis_client.delete(f"ab_test:active:{api_key.org_id}")
    except Exception:
        pass

    if payload.name is not None:
        test.name = payload.name
    if payload.model_a is not None:
        test.model_a = payload.model_a
    if payload.provider_a is not None:
        test.provider_a = payload.provider_a
    if payload.model_b is not None:
        test.model_b = payload.model_b
    if payload.provider_b is not None:
        test.provider_b = payload.provider_b
    if payload.split_pct is not None:
        test.split_pct = payload.split_pct
    if payload.ends_at is not None:
        test.ends_at = payload.ends_at

    if payload.status is not None:
        if payload.status == "active" and test.status != "active":
            # Verify no other test is active
            active_stmt = select(ABTest).where(
                ABTest.org_id == api_key.org_id,
                ABTest.status == "active",
                ABTest.id != test.id
            )
            active_res = await db.execute(active_stmt)
            if active_res.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Another active A/B test already exists. Pause or complete it first."
                )
            test.status = "active"
        elif payload.status == "completed" and test.status != "completed":
            test.status = "completed"
            test.ends_at = datetime.now(timezone.utc)
            # Compute results summary and cache inside results column
            summary = await _calculate_metrics(test, db)
            test.results = summary
        elif payload.status in ("paused", "active", "completed"):
            test.status = payload.status

    db.add(test)
    await db.commit()
    await db.refresh(test)
    return test


@router.get("/ab-tests/{test_id}/results", response_model=ABTestResultsResponse)
async def get_ab_test_results(
    test_id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns results and performance metrics comparing the two variants.
    """
    stmt = select(ABTest).where(
        ABTest.id == test_id,
        ABTest.org_id == api_key.org_id
    )
    result = await db.execute(stmt)
    test = result.scalars().first()
    if not test:
        raise HTTPException(status_code=404, detail="A/B test not found.")

    if test.status == "completed" and test.results:
        return test.results

    # Calculate live results
    return await _calculate_metrics(test, db)


async def _calculate_metrics(test: ABTest, db: AsyncSession) -> dict:
    from sqlalchemy import func
    from app.routing.ab_stats import calculate_significance
    from app.models.ab_test import ShadowResult

    shadow_pairs = 0
    if getattr(test, "test_mode", "traffic_split") == "shadow":
        stmt = select(
            func.count(ShadowResult.id).label("requests"),
            func.sum(ShadowResult.model_a_cost).label("a_total_cost"),
            func.avg(ShadowResult.model_a_cost).label("a_avg_cost"),
            func.avg(ShadowResult.model_a_latency).label("a_avg_latency"),
            func.sum(ShadowResult.model_b_cost).label("b_total_cost"),
            func.avg(ShadowResult.model_b_cost).label("b_avg_cost"),
            func.avg(ShadowResult.model_b_latency).label("b_avg_latency")
        ).where(
            ShadowResult.test_id == test.id
        )
        res = await db.execute(stmt)
        row = res.first()
        shadow_pairs = int(row.requests or 0)

        stats = {
            "A": {
                "requests": shadow_pairs,
                "total_cost": float(row.a_total_cost or 0.0),
                "avg_cost": float(row.a_avg_cost or 0.0),
                "avg_latency": float(row.a_avg_latency or 0.0)
            },
            "B": {
                "requests": shadow_pairs,
                "total_cost": float(row.b_total_cost or 0.0),
                "avg_cost": float(row.b_avg_cost or 0.0),
                "avg_latency": float(row.b_avg_latency or 0.0)
            }
        }

        # Query all costs for significance calculation
        stmt_costs_a = select(ShadowResult.model_a_cost).where(ShadowResult.test_id == test.id)
        stmt_costs_b = select(ShadowResult.model_b_cost).where(ShadowResult.test_id == test.id)
        costs_a_res = await db.execute(stmt_costs_a)
        costs_b_res = await db.execute(stmt_costs_b)
        results_a = [r[0] for r in costs_a_res.all()]
        results_b = [r[0] for r in costs_b_res.all()]

    else:
        stmt = select(
            ABTestResult.variant,
            func.count(ABTestResult.id).label("requests"),
            func.sum(ABTestResult.cost).label("total_cost"),
            func.avg(ABTestResult.cost).label("avg_cost"),
            func.avg(ABTestResult.latency).label("avg_latency")
        ).where(
            ABTestResult.test_id == test.id
        ).group_by(ABTestResult.variant)

        res = await db.execute(stmt)
        rows = res.all()

        stats = {
            "A": {"requests": 0, "avg_cost": 0.0, "avg_latency": 0.0, "total_cost": 0.0},
            "B": {"requests": 0, "avg_cost": 0.0, "avg_latency": 0.0, "total_cost": 0.0}
        }

        for row in rows:
            v = row.variant
            if v in stats:
                stats[v]["requests"] = int(row.requests or 0)
                stats[v]["total_cost"] = float(row.total_cost or 0.0)
                stats[v]["avg_cost"] = float(row.avg_cost or 0.0)
                stats[v]["avg_latency"] = float(row.avg_latency or 0.0)

        # Query all costs for significance calculation
        stmt_costs_a = select(ABTestResult.cost).where(ABTestResult.test_id == test.id, ABTestResult.variant == "A")
        stmt_costs_b = select(ABTestResult.cost).where(ABTestResult.test_id == test.id, ABTestResult.variant == "B")
        costs_a_res = await db.execute(stmt_costs_a)
        costs_b_res = await db.execute(stmt_costs_b)
        results_a = [r[0] for r in costs_a_res.all()]
        results_b = [r[0] for r in costs_b_res.all()]

    # Calculate significance
    sig_res = calculate_significance(results_a, results_b)

    requests_a = stats["A"]["requests"]
    requests_b = stats["B"]["requests"]
    avg_cost_a = stats["A"]["avg_cost"]
    avg_cost_b = stats["B"]["avg_cost"]
    avg_latency_a = stats["A"]["avg_latency"]
    avg_latency_b = stats["B"]["avg_latency"]

    # Calculate differences
    if avg_cost_a > 0:
        cost_diff = ((avg_cost_b - avg_cost_a) / avg_cost_a) * 100.0
    else:
        cost_diff = 0.0

    if avg_latency_a > 0:
        latency_diff = ((avg_latency_b - avg_latency_a) / avg_latency_a) * 100.0
    else:
        latency_diff = 0.0

    # Winner choice
    winner = sig_res.winner if sig_res.significant else "inconclusive"

    return {
        "model_a": stats["A"],
        "model_b": stats["B"],
        "winner": winner,
        "cost_difference_pct": round(cost_diff, 2),
        "latency_difference_pct": round(latency_diff, 2),
        "significance": {
            "significant": sig_res.significant,
            "p_value": sig_res.p_value,
            "confidence": sig_res.confidence,
            "requests_needed": sig_res.requests_needed,
            "winner": sig_res.winner,
            "effect_size": sig_res.effect_size
        },
        "mode": getattr(test, "test_mode", "traffic_split"),
        "shadow_pairs": shadow_pairs if getattr(test, "test_mode", "traffic_split") == "shadow" else None
    }
