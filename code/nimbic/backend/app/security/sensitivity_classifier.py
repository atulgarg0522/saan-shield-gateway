import re
from dataclasses import dataclass
from typing import List, Literal, Dict, Any


@dataclass
class SensitivityCategory:
    name: str  # "financial" | "legal" | "hr" | "medical" | "strategic" | "auth_credentials" | "custom_pattern"
    keywords_matched: List[str]
    confidence: float


@dataclass
class SensitivityResult:
    is_sensitive: bool
    categories: List[SensitivityCategory]
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float


class SensitivityClassifier:
    """
    Keyword and phrase density sensitivity classifier for outbound gateway prompts.
    Scans for HR, Financial, Legal, Strategic, and Authentication details.
    """

    async def analyze(self, text: str, custom_patterns: List[Dict[str, Any]] = None) -> SensitivityResult:
        if not text:
            return SensitivityResult(is_sensitive=False, categories=[], severity="low", confidence=0.0)

        # Keyword mapping categories definitions
        categories_dict = {
            "financial": [
                "revenue", "profit", "EBITDA", "quarterly results", "earnings", 
                "valuation", "term sheet", "cap table", "runway"
            ],
            "legal": [
                "attorney-client", "privileged", "confidential settlement", "NDA", 
                "non-disclosure", "litigation", "injunction", "cease and desist"
            ],
            "hr": [
                "salary", "compensation", "performance review", "termination", 
                "PIP", "equity", "stock options", "headcount", "layoff"
            ],
            "medical": [
                "diagnosis", "prescription", "PHI", "HIPAA", 
                "patient record", "medical history", "treatment plan"
            ],
            "strategic": [
                "acquisition", "merger", "competitive intelligence", "unreleased", 
                "roadmap Q", "go-to-market", "pricing strategy"
            ],
            "auth_credentials": [
                "password is", "my password", "login credentials", "api key is", "secret key is"
            ]
        }

        # Context signals that increase confidence of sensitivity (boost)
        boost_keywords = ["confidential", "strictly confidential", "do not distribute", "private", "internal-only"]
        has_boost = any(re.search(rf"\b{re.escape(bk)}\b", text, re.IGNORECASE) for bk in boost_keywords)

        matched_categories: List[SensitivityCategory] = []
        highest_severity: Literal["low", "medium", "high", "critical"] = "low"
        
        # Track overall confidence
        max_confidence = 0.0

        # Scan custom patterns first
        if custom_patterns:
            matched_custom = []
            for cp in custom_patterns:
                name = cp.get("name", "custom")
                regex = cp.get("regex") or cp.get("pattern")
                if regex:
                    try:
                        matches = re.findall(regex, text, re.IGNORECASE)
                        if matches:
                            matched_custom.append(name)
                    except Exception:
                        pass
            if matched_custom:
                matched_categories.append(SensitivityCategory(
                    name="custom_pattern",
                    keywords_matched=matched_custom,
                    confidence=1.0
                ))
                max_confidence = 1.0

        for cat_name, keywords in categories_dict.items():
            matched_words = []
            freq = 0
            
            for keyword in keywords:
                # Use regex with word boundaries for keywords, but handle spaces safely for phrases
                pattern = rf"\b{re.escape(keyword)}\b"
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    matched_words.append(keyword)
                    freq += len(matches)

            if matched_words:
                unique_count = len(matched_words)
                # Compute confidence based on unique matches and frequency
                if cat_name == "auth_credentials":
                    # Auth credentials always carry high confidence if matched
                    confidence = max(0.8, min(1.0, (unique_count * 0.3) + (freq * 0.05)))
                else:
                    confidence = min(1.0, (unique_count * 0.25) + (freq * 0.05))
                    # Context boost
                    if has_boost:
                        confidence = min(1.0, confidence + 0.10)

                # Keep track of maximum confidence score across matches
                if confidence > max_confidence:
                    max_confidence = confidence

                matched_categories.append(SensitivityCategory(
                    name=cat_name,
                    keywords_matched=matched_words,
                    confidence=confidence
                ))

        # Check if anything matched
        is_sensitive = len(matched_categories) > 0

        # Assess overall severity
        if is_sensitive:
            # Rank severities: low < medium < high < critical
            rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
            severity_rank = 1  # Base is low

            for cat in matched_categories:
                if cat.name == "custom_pattern":
                    current_rank = 3  # High severity for custom patterns matching
                elif cat.name == "auth_credentials":
                    # Credentials map to critical
                    current_rank = 4
                elif cat.confidence >= 0.70:
                    current_rank = 3  # High
                elif cat.confidence >= 0.30:
                    current_rank = 2  # Medium
                else:
                    current_rank = 1  # Low

                if current_rank > severity_rank:
                    severity_rank = current_rank

            # Map back to string
            rank_map = {1: "low", 2: "medium", 3: "high", 4: "critical"}
            highest_severity = rank_map[severity_rank]

        return SensitivityResult(
            is_sensitive=is_sensitive,
            categories=matched_categories,
            severity=highest_severity,
            confidence=max_confidence
        )
