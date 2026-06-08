import re
import ipaddress
import secrets
import structlog
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

# Presidio imports
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine

# DB imports
from app.models.security_policy import SecurityPolicy, PiiActionEnum, PolicyActionEnum
from app.models.security_violation import SecurityViolation, ViolationTypeEnum, SeverityEnum, ViolationActionEnum
from app.redis import redis_client

logger = structlog.get_logger()

# Initialize Presidio engines
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

# Custom Pattern Recognizers for Indian PII
aadhaar_pattern = Pattern(
    name="aadhaar_pattern",
    regex=r"\b[2-9]\d{3}\s\d{4}\s\d{4}\b|\b[2-9]\d{11}\b",
    score=0.85
)
aadhaar_recognizer = PatternRecognizer(
    supported_entity="AADHAAR",
    patterns=[aadhaar_pattern]
)

pan_pattern = Pattern(
    name="pan_pattern",
    regex=r"\b[A-Z]{5}\d{4}[A-Z]\b",
    score=0.85
)
pan_recognizer = PatternRecognizer(
    supported_entity="PAN",
    patterns=[pan_pattern]
)

analyzer.registry.add_recognizer(aadhaar_recognizer)
analyzer.registry.add_recognizer(pan_recognizer)


# --- GEOIP LOOKUP SERVICE ---

class GeoIPService:
    """
    Looks up country codes for IP addresses, utilizing an async HTTP lookup with Redis caching.
    Supports localhost/private IP identification to avoid remote lookup overhead.
    """
    
    @staticmethod
    async def get_country_code(ip: str) -> str:
        if not ip or ip in ("127.0.0.1", "localhost", "::1", "testclient"):
            return "US" # Default for local tests
            
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private or ip_obj.is_loopback:
                return "US"
        except ValueError:
            pass
            
        # Check Redis cache first
        cache_key = f"geoip:{ip}"
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return cached.decode("utf-8")
        except Exception as e:
            await logger.awarn("Failed to read GeoIP cache from Redis", error=str(e))
            
        # Fetch from ipapi.co (or ip-api.com) with 5s timeout
        country_code = "US" # Default fallback
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"https://ipapi.co/{ip}/json/")
                if response.status_code == 200:
                    data = response.json()
                    country_code = data.get("country_code", "US").upper()
        except Exception as e:
            await logger.awarn("Failed to fetch GeoIP details from remote service", ip=ip, error=str(e))
            
        # Cache for 24 hours
        try:
            await redis_client.setex(cache_key, 86400, country_code.encode("utf-8"))
        except Exception as e:
            await logger.awarn("Failed to write GeoIP cache to Redis", error=str(e))
            
        return country_code


# --- POLICY EVALUATION ENGINE ---

async def get_or_create_policy(org_id: Any, db: AsyncSession) -> SecurityPolicy:
    """
    Returns the organization's SecurityPolicy or seeds a default one if absent.
    """
    stmt = select(SecurityPolicy).where(SecurityPolicy.org_id == org_id)
    res = await db.execute(stmt)
    policy = res.scalars().first()
    
    if not policy:
        policy = SecurityPolicy(
            org_id=org_id,
            pii_action=PiiActionEnum.redact,
            code_action=PolicyActionEnum.warn,
            sensitive_action=PolicyActionEnum.warn,
            blocked_regions=[],
            allowed_providers_by_region={},
            custom_patterns=[],
            is_active=True
        )
        db.add(policy)
        await db.commit()
        await db.refresh(policy)
        
    return policy


def detect_source_code(text: str) -> bool:
    """
    Detects if a string contains software source code snippets using regex heuristics.
    """
    code_patterns = [
        r"\bdef\s+\w+\s*\(.*?\)\s*:",
        r"\bclass\s+\w+\s*[:{]",
        r"\bimport\s+[\w\s,]+(\s+as\s+\w+)?\b",
        r"\bfrom\s+[\w\.]+\s+import\s+[\w\s,\*]+\b",
        r"\bfunction\s+\w*\s*\(.*?\)\s*\{",
        r"\bconst\s+\w+\s*=\s*.*?=>",
        r"\b(?:const|let|var)\s+\w+\s*=\s*(?:require|async|function|\d+|['\"[{])",
        r"\bfunc\s+\w+\s*\(.*?\)\s*.*?\{",
        r"\bpackage\s+[\w\.]+\b",
        r"#include\s*<[\w\.]+>",
        r"\bpublic\s+static\s+void\s+main\b",
        r"\bcout\s*<<\s*",
        r"\bsystem\.out\.println\b",
    ]
    matches = 0
    for pattern in code_patterns:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            matches += 1
            if matches >= 2:
                return True
                
    # Direct strong indicators
    strong_patterns = [
        r"\bdef\s+\w+\s*\(.*?\)\s*:",
        r"\bfunction\s+\w+\s*\(.*?\)\s*\{",
        r"\bfrom\s+[\w\.]+\s+import\s+[\w\s,\*]+\b",
        r"#include\s*<[\w\.]+>"
    ]
    for pattern in strong_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
            
    return False


def detect_sensitive_content(text: str, custom_patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Scans the prompt text for contractual, financial, and custom regex-defined keywords.
    """
    triggered = []
    
    standard_patterns = [
        (r"\bnon-disclosure\s+agreement\b|\bNDA\b", "NDA/Contractual"),
        (r"\bconfidentiality\s+agreement\b", "NDA/Contractual"),
        (r"\bbalance\s+sheet\b|\bfinancial\s+statement\b", "Financials"),
        (r"\brevenue\s+projection\b|\bprofit\s+forecast\b", "Financials"),
        (r"\btrade\s+secret\b", "Proprietary Info"),
        (r"\bproprietary\s+information\b", "Proprietary Info"),
        (r"\bstrictly\s+confidential\b", "Confidentiality Marker"),
    ]
    
    for regex_str, label in standard_patterns:
        if re.search(regex_str, text, re.IGNORECASE):
            triggered.append({"label": label, "pattern": regex_str})
            
    # Apply custom patterns
    for custom in custom_patterns:
        name = custom.get("name", "custom")
        regex = custom.get("regex")
        if regex:
            try:
                if re.search(regex, text, re.IGNORECASE):
                    triggered.append({"label": name, "pattern": regex})
            except Exception:
                pass
                
    return triggered


async def log_violation(
    org_id: Any,
    request_id: str,
    violation_type: ViolationTypeEnum,
    severity: SeverityEnum,
    action_taken: ViolationActionEnum,
    details: Dict[str, Any],
    prompt_snippet: Optional[str],
    db: AsyncSession
) -> None:
    """
    Inserts a security violation log row into the security_violations database table.
    """
    snippet = prompt_snippet[:197] + "..." if prompt_snippet and len(prompt_snippet) > 200 else prompt_snippet
    violation = SecurityViolation(
        org_id=org_id,
        request_id=request_id,
        violation_type=violation_type,
        severity=severity,
        action_taken=action_taken,
        details=details,
        prompt_snippet=snippet
    )
    db.add(violation)
    await db.commit()
    await logger.ainfo("Security violation logged", violation_type=violation_type, action_taken=action_taken)


async def check_security_policies(
    org_id: Any,
    messages: List[Dict[str, str]],
    provider: str,
    client_ip: str,
    request_id: str,
    db: AsyncSession
) -> Tuple[List[Dict[str, str]], bool, Optional[str]]:
    """
    Evaluates prompt messages against the organization's SecurityPolicy.
    Returns:
      Tuple[mutated_messages, is_blocked, block_reason]
    """
    from app.security.policy_engine import PolicyEngine
    from app.redis import redis_client
    from app.models.security_violation import SecurityViolation, ViolationTypeEnum, SeverityEnum, ViolationActionEnum

    engine = PolicyEngine()
    mutated_messages = []
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # We only inspect user input prompts
        if role != "user" or not content:
            mutated_messages.append(msg)
            continue
            
        decision = await engine.evaluate(
            prompt=content,
            request_ip=client_ip,
            provider=provider,
            org_id=org_id,
            db=db,
            redis=redis_client,
            request_id=request_id
        )
        
        # Synchronously write violations to DB for test assertion matching
        if decision.violations:
            for v in decision.violations:
                violation_db = SecurityViolation(
                    org_id=org_id,
                    request_id=request_id,
                    violation_type=ViolationTypeEnum(v.violation_type),
                    severity=SeverityEnum(v.severity),
                    action_taken=ViolationActionEnum(v.action_applied),
                    details=v.details,
                    prompt_snippet=content[:197] + "..." if len(content) > 200 else content
                )
                db.add(violation_db)
            await db.commit()
        
        if decision.action == "block":
            block_reason = "Request blocked by organization security policy."
            if decision.violations:
                for v in decision.violations:
                    if v.action_applied == "blocked":
                        if v.violation_type == "data_residency":
                            block_reason = v.details.get("reason") or block_reason
                        elif v.violation_type == "pii":
                            block_reason = "Prompt blocks sending personally identifiable information (PII) under company policies."
                        elif v.violation_type == "source_code":
                            block_reason = "Proprietary source code posting is blocked by organization security policy."
                        elif v.violation_type == "sensitive_content":
                            block_reason = "Prompt contains restricted sensitive or proprietary terms."
                        break
            return messages, True, block_reason
            
        elif decision.action == "redact":
            mutated_messages.append({"role": role, "content": decision.final_prompt})
        else:
            mutated_messages.append({"role": role, "content": content})
            
    return mutated_messages, False, None
