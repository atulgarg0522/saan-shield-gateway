import secrets
import json
import asyncio
from dataclasses import dataclass
from typing import List, Literal, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

# Models
from app.models.security_policy import SecurityPolicy
from app.services.security_svc import get_or_create_policy

# Detectors
from app.security.pii_detector import PIIDetector
from app.security.code_detector import CodeDetector
from app.security.sensitivity_classifier import SensitivityClassifier
from app.security.residency_enforcer import ResidencyEnforcer

logger = structlog.get_logger()


@dataclass
class ViolationRecord:
    violation_type: str
    severity: str
    details: dict
    action_applied: str


@dataclass
class PolicyDecision:
    action: Literal["allow", "redact", "warn", "block"]
    final_prompt: str
    violations: List[ViolationRecord]
    should_log_violation: bool
    block_reason: Optional[str]


class PolicyEngine:
    """
    Central orchestration security compliance check engine for outbound LLM prompts.
    Coordinates geofencing, PII detection layers, source code heuristics, and sensitive content matches in parallel.
    """

    def __init__(self):
        self.residency_enforcer = ResidencyEnforcer()
        self.pii_detector = PIIDetector()
        self.code_detector = CodeDetector()
        self.sensitivity_classifier = SensitivityClassifier()

    async def get_cached_policy(self, org_id: str, db: AsyncSession, redis: Any) -> Any:
        """
        Loads the organization's SecurityPolicy from DB with 5 min Redis caching.
        """
        cache_key = f"policy:org:{org_id}"
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    data = json.loads(cached.decode("utf-8"))
                    
                    class CachedPolicy:
                        def __init__(self, d):
                            self.is_active = d["is_active"]
                            
                            class ValueHolder:
                                def __init__(self, val):
                                    self.value = val
                                def __str__(self):
                                    return self.value
                                def __eq__(self, other):
                                    if hasattr(other, 'value'):
                                        return self.value == other.value
                                    return self.value == other
                            
                            self.pii_action = ValueHolder(d["pii_action"])
                            self.code_action = ValueHolder(d["code_action"])
                            self.sensitive_action = ValueHolder(d["sensitive_action"])
                            self.blocked_regions = d["blocked_regions"]
                            self.allowed_providers_by_region = d["allowed_providers_by_region"]
                            self.custom_patterns = d["custom_patterns"]
                            
                    return CachedPolicy(data)
            except Exception as e:
                logger.warn("Failed to get policy from Redis cache", error=str(e))

        # DB fallback on cache miss
        policy = await get_or_create_policy(org_id, db)
        
        # Cache in Redis for 5 minutes
        if redis:
            try:
                policy_dict = {
                    "is_active": policy.is_active,
                    "pii_action": policy.pii_action.value if hasattr(policy.pii_action, "value") else str(policy.pii_action),
                    "code_action": policy.code_action.value if hasattr(policy.code_action, "value") else str(policy.code_action),
                    "sensitive_action": policy.sensitive_action.value if hasattr(policy.sensitive_action, "value") else str(policy.sensitive_action),
                    "blocked_regions": list(policy.blocked_regions),
                    "allowed_providers_by_region": dict(policy.allowed_providers_by_region),
                    "custom_patterns": list(policy.custom_patterns)
                }
                await redis.setex(cache_key, 300, json.dumps(policy_dict).encode("utf-8"))
            except Exception as e:
                logger.warn("Failed to cache policy in Redis", error=str(e))

        return policy

    async def evaluate(
        self,
        prompt: str,
        request_ip: str,
        provider: str,
        org_id: Any,
        db: AsyncSession,
        redis,
        request_id: Optional[str] = None
    ) -> PolicyDecision:
        # 1. Fetch organization active security policy (cached)
        policy = await self.get_cached_policy(org_id, db, redis)
        
        if not policy.is_active:
            return PolicyDecision(
                action="allow",
                final_prompt=prompt,
                violations=[],
                should_log_violation=False,
                block_reason=None
            )

        # 2. Run all 4 detectors in parallel using asyncio.gather()
        pii_task = self.pii_detector.analyze(prompt, str(org_id), policy.custom_patterns)
        code_task = self.code_detector.analyze(prompt)
        sens_task = self.sensitivity_classifier.analyze(prompt, policy.custom_patterns)
        residency_task = self.residency_enforcer.check(request_ip, provider, str(org_id), policy)

        pii_res, code_res, sens_res, residency_res = await asyncio.gather(
            pii_task,
            code_task,
            sens_task,
            residency_task
        )

        violations: List[ViolationRecord] = []
        final_prompt = prompt
        final_action: Literal["allow", "redact", "warn", "block"] = "allow"
        block_reason: Optional[str] = None

        # --- 3. For each detector result, apply the configured action ---

        # Data Residency Fail -> always block (no override)
        if not residency_res.is_allowed:
            final_action = "block"
            block_reason = residency_res.blocked_reason
            violations.append(ViolationRecord(
                violation_type="data_residency",
                severity="high" if residency_res.suggested_provider else "critical",
                details={
                    "client_ip": request_ip,
                    "country": residency_res.request_country,
                    "region": residency_res.request_region,
                    "provider": provider,
                    "reason": residency_res.blocked_reason,
                    "suggested_provider": residency_res.suggested_provider
                },
                action_applied="blocked"
            ))

        # PII Action Mapping
        if pii_res.has_pii:
            pii_act = policy.pii_action.value if hasattr(policy.pii_action, "value") else str(policy.pii_action)
            if pii_act != "allow":
                action_applied_map = {"redact": "redacted", "warn": "warned", "block": "blocked"}
                action_applied = action_applied_map.get(pii_act, "warned")
                violations.append(ViolationRecord(
                    violation_type="pii",
                    severity=pii_res.severity,
                    details={
                        "entities": [
                            {
                                "type": e.entity_type,
                                "start": e.start,
                                "end": e.end,
                                "score": e.score,
                                "source": e.source
                            } for e in pii_res.entities
                        ]
                    },
                    action_applied=action_applied
                ))

                if pii_act == "block":
                    final_action = "block"
                    if not block_reason:
                        block_reason = "Prompt blocks sending personally identifiable information (PII) under company policies."
                elif pii_act == "redact" and final_action != "block":
                    final_action = "redact"
                    final_prompt = pii_res.redacted_text
                elif pii_act == "warn" and final_action not in ("block", "redact"):
                    final_action = "warn"

        # Code Action Mapping
        if code_res.has_code:
            code_act = policy.code_action.value if hasattr(policy.code_action, "value") else str(policy.code_action)
            if code_act != "allow":
                action_applied = "blocked" if code_act == "block" else "warned"
                violations.append(ViolationRecord(
                    violation_type="source_code",
                    severity=code_res.severity,
                    details={
                        "languages": code_res.languages,
                        "snippets": [
                            {
                                "language": s.language,
                                "snippet": s.snippet,
                                "start_line": s.start_line,
                                "indicator_type": s.indicator_type
                            } for s in code_res.snippets
                        ]
                    },
                    action_applied=action_applied
                ))

                if code_act == "block":
                    final_action = "block"
                    if not block_reason:
                        block_reason = "Proprietary source code posting is blocked by organization security policy."
                elif code_act == "warn" and final_action not in ("block", "redact"):
                    final_action = "warn"

        # Sensitive Action Mapping
        if sens_res.is_sensitive:
            sens_act = policy.sensitive_action.value if hasattr(policy.sensitive_action, "value") else str(policy.sensitive_action)
            if sens_act != "allow":
                action_applied = "blocked" if sens_act == "block" else "warned"
                violations.append(ViolationRecord(
                    violation_type="sensitive_content",
                    severity=sens_res.severity,
                    details={
                        "confidence": sens_res.confidence,
                        "categories": [
                            {
                                "name": c.name,
                                "matched_words": c.keywords_matched,
                                "confidence": c.confidence
                            } for c in sens_res.categories
                        ]
                    },
                    action_applied=action_applied
                ))

                if sens_act == "block":
                    final_action = "block"
                    if not block_reason:
                        block_reason = "Prompt contains restricted sensitive or proprietary terms."
                elif sens_act == "warn" and final_action not in ("block", "redact"):
                    final_action = "warn"

        should_log_violation = len(violations) > 0

        return PolicyDecision(
            action=final_action,
            final_prompt=final_prompt,
            violations=violations,
            should_log_violation=should_log_violation,
            block_reason=block_reason
        )
