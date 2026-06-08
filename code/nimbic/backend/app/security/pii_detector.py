import re
import asyncio
from dataclasses import dataclass
from typing import List, Dict, Any, Literal, Optional
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern

# --- DATACLASSES ---

@dataclass
class PIIEntity:
    entity_type: str
    text: str
    start: int
    end: int
    score: float
    source: str  # "presidio" | "regex" | "custom"


@dataclass
class PIIResult:
    has_pii: bool
    entities: List[PIIEntity]
    redacted_text: str
    severity: Literal["low", "medium", "high", "critical"]


# --- PRESIDIO CUSTOM RECOGNIZERS (LAYER 2) ---

aadhaar_pattern = Pattern(
    name="aadhaar_pattern",
    regex=r"\b[2-9]{1}[0-9]{3}\s[0-9]{4}\s[0-9]{4}\b",
    score=0.85
)
aadhaar_rec = PatternRecognizer(supported_entity="AADHAAR", patterns=[aadhaar_pattern])

pan_pattern = Pattern(
    name="pan_pattern",
    regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b",
    score=0.85
)
pan_rec = PatternRecognizer(supported_entity="PAN", patterns=[pan_pattern])

mobile_pattern = Pattern(
    name="mobile_pattern",
    regex=r"\b[6-9]\d{9}\b",
    score=0.80
)
mobile_rec = PatternRecognizer(supported_entity="PHONE_NUMBER", patterns=[mobile_pattern])

upi_pattern = Pattern(
    name="upi_pattern",
    regex=r"\b[a-zA-Z0-9._-]+@[a-zA-Z0-9]+\b",
    score=0.85
)
upi_rec = PatternRecognizer(supported_entity="UPI_ID", patterns=[upi_pattern])

ifsc_pattern = Pattern(
    name="ifsc_pattern",
    regex=r"\b[A-Z]{4}0[A-Z0-9]{6}\b",
    score=0.85
)
ifsc_rec = PatternRecognizer(supported_entity="IFSC", patterns=[ifsc_pattern])

aws_pattern = Pattern(
    name="aws_pattern",
    regex=r"\bAKIA[0-9A-Z]{16}\b",
    score=0.95
)
aws_rec = PatternRecognizer(supported_entity="AWS_KEY", patterns=[aws_pattern])

private_key_pattern = Pattern(
    name="private_key_pattern",
    regex=r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    score=0.95
)
private_key_rec = PatternRecognizer(supported_entity="PRIVATE_KEY", patterns=[private_key_pattern])

generic_key_pattern = Pattern(
    name="generic_key_pattern",
    regex=r"\b(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[\w\-]{16,}\b",
    score=0.85
)
generic_key_rec = PatternRecognizer(supported_entity="GENERIC_KEY", patterns=[generic_key_pattern])


# --- ENGINE CACHING ---

_analyzer_engine: Optional[AnalyzerEngine] = None

def get_analyzer_engine() -> AnalyzerEngine:
    global _analyzer_engine
    if _analyzer_engine is None:
        # Initializing Presidio Analyzer (runs en_core_web_sm natively)
        _analyzer_engine = AnalyzerEngine()
        
        # Load and register custom Pattern Recognizers
        _analyzer_engine.registry.add_recognizer(aadhaar_rec)
        _analyzer_engine.registry.add_recognizer(pan_rec)
        _analyzer_engine.registry.add_recognizer(mobile_rec)
        _analyzer_engine.registry.add_recognizer(upi_rec)
        _analyzer_engine.registry.add_recognizer(ifsc_rec)
        _analyzer_engine.registry.add_recognizer(aws_rec)
        _analyzer_engine.registry.add_recognizer(private_key_rec)
        _analyzer_engine.registry.add_recognizer(generic_key_rec)
        
    return _analyzer_engine


# --- DETECTOR CLASS ---

class PIIDetector:
    """
    High-performance PII detection engine running Microsoft Presidio NLP (spaCy),
    custom regular expression recognizers, and tenant-scoped dynamic rules in parallel.
    """

    async def analyze(
        self,
        text: str,
        org_id: str,
        custom_patterns: List[Dict[str, Any]]
    ) -> PIIResult:
        if not text:
            return PIIResult(has_pii=False, entities=[], redacted_text="", severity="low")

        # 1. Run Presidio Analyzer (Layer 1 & Layer 2) in separate thread
        engine = get_analyzer_engine()
        presidio_entities = await asyncio.to_thread(
            engine.analyze,
            text=text,
            language="en",
            score_threshold=0.6
        )

        entities: List[PIIEntity] = []

        # Convert Presidio results to PIIEntity structure
        for result in presidio_entities:
            # Distinguish source between NLP (Presidio default) and Regex Pattern Recognizer
            source = "presidio"
            
            # Map standard recognizer names to regex if matching custom entities
            custom_types = {"AADHAAR", "PAN", "UPI_ID", "IFSC", "AWS_KEY", "PRIVATE_KEY", "GENERIC_KEY"}
            if result.entity_type in custom_types:
                source = "regex"
            elif result.entity_type == "PHONE_NUMBER" and any(r.name == "mobile_pattern" for r in engine.registry.get_recognizers(language="en", all_fields=True)):
                # Mobile regex pattern recognizer runs under PHONE_NUMBER, let's keep source regex if possible
                # For simplicity, if PHONE_NUMBER matches, it could be either. We check if text matches mobile regex:
                if re.match(r"^[6-9]\d{9}$", text[result.start:result.end]):
                    source = "regex"

            entities.append(PIIEntity(
                entity_type=result.entity_type,
                text=text[result.start:result.end],
                start=result.start,
                end=result.end,
                score=result.score,
                source=source
            ))

        # 2. Run Org Custom Patterns (Layer 3)
        for pattern_item in custom_patterns:
            name = pattern_item.get("name", "custom_pattern")
            pattern_str = pattern_item.get("pattern")
            if not pattern_str:
                continue
            try:
                rx = re.compile(pattern_str, re.IGNORECASE)
                for match in rx.finditer(text):
                    start, end = match.span()
                    val = match.group()
                    entities.append(PIIEntity(
                        entity_type=name.upper().replace(" ", "_"),
                        text=val,
                        start=start,
                        end=end,
                        score=1.0,
                        source="custom"
                    ))
            except Exception:
                pass

        # 3. Resolve Overlapping Entity Spans
        resolved_entities = self._resolve_overlaps(entities)

        # 4. Apply Redaction Logic
        redacted_text = self._redact_text(text, resolved_entities)

        # 5. Severity Assessment
        severity = self._assess_severity(resolved_entities)

        return PIIResult(
            has_pii=len(resolved_entities) > 0,
            entities=resolved_entities,
            redacted_text=redacted_text,
            severity=severity
        )

    def _resolve_overlaps(self, entities: List[PIIEntity]) -> List[PIIEntity]:
        """
        Sorts entities by starting boundary (and size descending) to yield a non-overlapping list.
        """
        sorted_entities = sorted(entities, key=lambda x: (x.start, -(x.end - x.start)))
        resolved: List[PIIEntity] = []
        last_end = -1
        for ent in sorted_entities:
            if ent.start >= last_end:
                resolved.append(ent)
                last_end = ent.end
        return resolved

    def _redact_text(self, text: str, entities: List[PIIEntity]) -> str:
        """
        Replaces detected spans with corresponding placeholders from back to front.
        """
        # Sort back to front to avoid shifting index coordinates
        sorted_back = sorted(entities, key=lambda x: x.start, reverse=True)
        chars = list(text)

        for ent in sorted_back:
            placeholder = self._get_placeholder(ent.entity_type)
            chars[ent.start:ent.end] = list(placeholder)

        return "".join(chars)

    def _get_placeholder(self, entity_type: str) -> str:
        upper_type = entity_type.upper()
        if upper_type in ("EMAIL_ADDRESS", "EMAIL"):
            return "[EMAIL]"
        if upper_type == "PHONE_NUMBER":
            return "[PHONE]"
        if upper_type in ("AWS_KEY", "PRIVATE_KEY", "GENERIC_KEY"):
            return "[SECRET_REDACTED]"
        if upper_type in ("PAN", "IN_PAN"):
            return "[PAN]"
        if upper_type in ("AADHAAR", "IN_AADHAAR"):
            return "[AADHAAR]"
        
        return f"[{upper_type}]"

    def _assess_severity(self, entities: List[PIIEntity]) -> Literal["low", "medium", "high", "critical"]:
        if not entities:
            return "low"

        severity_map = {
            # Critical
            "PRIVATE_KEY": "critical",
            "AWS_KEY": "critical",
            "GENERIC_KEY": "critical",
            "AADHAAR": "critical",
            "IN_AADHAAR": "critical",
            "PAN": "critical",
            "IN_PAN": "critical",
            "CREDIT_CARD": "critical",
            "US_SSN": "critical",
            
            # High
            
            # Medium
            "EMAIL": "medium",
            "EMAIL_ADDRESS": "medium",
            "PHONE_NUMBER": "medium",
            "IP_ADDRESS": "medium",
            "UPI_ID": "medium",
            "IFSC": "medium",
            
            # Low
            "PERSON": "low",
            "LOCATION": "low",
            "DATE_TIME": "low",
            "NRP": "low"
        }

        highest: Literal["low", "medium", "high", "critical"] = "low"
        rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}

        for ent in entities:
            sev = severity_map.get(ent.entity_type.upper(), "low")
            if rank[sev] > rank[highest]:
                highest = sev

        return highest
