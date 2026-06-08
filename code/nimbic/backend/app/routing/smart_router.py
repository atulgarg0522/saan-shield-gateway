import json
from decimal import Decimal
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional
from sqlalchemy import select
from app.routing.prompt_classifier import PromptClassification
from app.services.cost_svc import MODEL_COST_TABLE


@dataclass
class RouteDecision:
    provider: str
    model: str
    fallback_chain: List[Tuple[str, str]]
    routing_reason: str
    estimated_cost_usd: Decimal
    baseline_cost_usd: Decimal


class SmartRouter:
    """
    SmartRouter decides which provider and model to route a prompt to,
    evaluating custom organization routing rules and falling back to default
    complexity-based suggestions. It also constructs a multi-provider fallback chain.
    """

    async def route(
        self,
        classification: PromptClassification,
        org_id: str,
        db,
        redis,
        metadata: Optional[Dict[str, Any]] = None
    ) -> RouteDecision:
        import uuid
        try:
            org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
        except ValueError:
            org_uuid = org_id
        metadata = metadata or {}
        
        # --- LAYER 1: Check Org Routing Rules (Redis Cached 5 mins) ---
        redis_key = f"routing_rules:{org_id}"
        rules = None
        try:
            cached_rules = await redis.get(redis_key)
            if cached_rules:
                rules = json.loads(cached_rules)
        except Exception:
            pass

        if rules is None:
            # Query Database
            from app.models.routing_cache import RoutingRule
            stmt = select(RoutingRule).where(
                RoutingRule.org_id == org_uuid,
                RoutingRule.is_active == True
            ).order_by(RoutingRule.priority.asc())
            
            result = await db.execute(stmt)
            db_rules = result.scalars().all()
            
            rules = []
            for r in db_rules:
                rules.append({
                    "name": r.name,
                    "conditions": r.conditions,
                    "target_model": r.target_model,
                    "target_provider": r.target_provider,
                    "priority": r.priority
                })
            
            # Cache in Redis for 5 minutes (300 seconds)
            try:
                await redis.setex(redis_key, 300, json.dumps(rules))
            except Exception:
                pass

        # Evaluate rules in order of priority (first match wins)
        matched_rule = None
        for rule in rules:
            if self._evaluate_conditions(rule["conditions"], classification, metadata):
                matched_rule = rule
                break

        if matched_rule:
            provider = matched_rule["target_provider"]
            model = matched_rule["target_model"]
            reason = f"org rule match: {matched_rule['name']}"
        else:
            # --- LAYER 2: Default Complexity-Based Routing ---
            if classification.complexity == "simple":
                model = "claude-haiku-4-5"
                provider = "anthropic"
            elif classification.complexity == "complex":
                model = "claude-opus-4-6"
                provider = "anthropic"
            else:
                model = "claude-sonnet-4-6"
                provider = "anthropic"
            reason = f"complexity default: {classification.complexity} -> {model}"

        # --- LAYER 3: Build Fallback Chain ---
        fallback_chain = self._build_fallback_chain(provider, model)

        # Resolve org's configured providers to append most reliable active configuration as last resort
        try:
            from app.models.provider_config import ProviderConfig
            stmt_active = select(ProviderConfig).where(
                ProviderConfig.org_id == org_uuid,
                ProviderConfig.is_active == True
            )
            configs = (await db.execute(stmt_active)).scalars().all()
            active_providers = {c.provider.value for c in configs}
            
            # Always ensure the org has a backup. If they have 'openai' active but primary is 'anthropic',
            # append OpenAI as a last resort fallback.
            if "openai" in active_providers and provider != "openai":
                last_resort = ("openai", "gpt-4o")
                if last_resort not in fallback_chain:
                    fallback_chain.append(last_resort)
            elif "anthropic" in active_providers and provider != "anthropic":
                last_resort = ("anthropic", "claude-sonnet-4-6")
                if last_resort not in fallback_chain:
                    fallback_chain.append(last_resort)
        except Exception:
            pass

        # --- LAYER 4: Cost Estimations ---
        estimated_tokens = classification.estimated_tokens
        
        rates = MODEL_COST_TABLE.get(model, {"input": Decimal("0.003")})
        estimated_cost = (Decimal(estimated_tokens) * rates["input"]) / Decimal("1000")
        
        baseline_rates = MODEL_COST_TABLE["gpt-4o"]
        baseline_cost = (Decimal(estimated_tokens) * baseline_rates["input"]) / Decimal("1000")

        return RouteDecision(
            provider=provider,
            model=model,
            fallback_chain=fallback_chain,
            routing_reason=reason,
            estimated_cost_usd=estimated_cost.quantize(Decimal("1.000000")),
            baseline_cost_usd=baseline_cost.quantize(Decimal("1.000000"))
        )

    def _evaluate_conditions(
        self,
        conditions: Dict[str, Any],
        classification: PromptClassification,
        metadata: Dict[str, Any]
    ) -> bool:
        """
        Returns True if all conditions specified in the rule match the prompt classification or metadata.
        """
        for key, value in conditions.items():
            if key == "complexity":
                if classification.complexity != value:
                    return False
            elif key == "category":
                if classification.category != value:
                    return False
            elif key == "max_tokens":
                if classification.estimated_tokens > value:
                    return False
            elif key == "user_tag":
                user_tag = metadata.get("user_tag")
                if user_tag != value:
                    return False
            else:
                # Fallback checking metadata keys
                if metadata.get(key) != value:
                    return False
        return True

    def _build_fallback_chain(self, provider: str, model: str) -> List[Tuple[str, str]]:
        """
        Builds a fallback list of 1-2 alternative models of increasing capability.
        """
        chain = []
        if provider == "anthropic":
            if model == "claude-haiku-4-5":
                chain.append(("anthropic", "claude-sonnet-4-6"))
                chain.append(("anthropic", "claude-opus-4-6"))
            elif model == "claude-sonnet-4-6":
                chain.append(("anthropic", "claude-opus-4-6"))
                chain.append(("openai", "gpt-4o"))
            else:  # claude-opus-4-6
                chain.append(("openai", "gpt-4o"))
                chain.append(("openai", "gpt-4-turbo"))
        elif provider == "openai":
            if model == "gpt-4o-mini":
                chain.append(("openai", "gpt-4o"))
                chain.append(("openai", "gpt-4-turbo"))
            elif model == "gpt-4o":
                chain.append(("openai", "gpt-4-turbo"))
                chain.append(("anthropic", "claude-opus-4-6"))
            else:
                chain.append(("openai", "gpt-4o"))
                chain.append(("anthropic", "claude-sonnet-4-6"))
        elif provider == "gemini":
            if model == "gemini-3-5-flash":
                chain.append(("gemini", "gemini-3-pro"))
                chain.append(("openai", "gpt-4o"))
            else:
                chain.append(("openai", "gpt-4o"))
                chain.append(("anthropic", "claude-opus-4-6"))
        else:
            # Default generic fallback chain
            chain.append(("openai", "gpt-4o-mini"))
            chain.append(("openai", "gpt-4o"))

        return chain
