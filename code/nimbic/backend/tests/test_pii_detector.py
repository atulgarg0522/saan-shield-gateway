import pytest
from app.security.pii_detector import PIIDetector, PIIEntity


@pytest.mark.asyncio
async def test_pii_detector_layers():
    detector = PIIDetector()

    # 1. Test Layer 1 — Standard Presidio
    result = await detector.analyze(
        text="Hello John, my email is john@example.com",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result.has_pii is True
    assert result.severity == "medium"  # Email is medium
    assert "[PERSON]" in result.redacted_text
    assert "[EMAIL]" in result.redacted_text

    # 2. Test Layer 2 — Custom regex recognizers (Aadhaar, PAN, Mobile, Keys)
    text_custom_regex = (
        "My Aadhaar is 2345 6789 0123. "
        "PAN card number is ABCDE1234F. "
        "UPI handle: atul@okaxis. "
        "AWS credentials: AKIAIOSFODNN7EXAMPLE. "
        "My private key block is -----BEGIN RSA PRIVATE KEY-----"
    )
    result_regex = await detector.analyze(
        text=text_custom_regex,
        org_id="test_org",
        custom_patterns=[]
    )
    assert result_regex.has_pii is True
    # Private key / AWS key raises severity to critical
    assert result_regex.severity == "critical"
    assert "[AADHAAR]" in result_regex.redacted_text
    assert "[PAN]" in result_regex.redacted_text
    assert "[UPI_ID]" in result_regex.redacted_text
    assert "[SECRET_REDACTED]" in result_regex.redacted_text

    # 3. Test Layer 3 — Org custom patterns
    custom_patterns = [
        {"name": "internal_codename", "pattern": r"Project-[XYZ]"}
    ]
    result_org = await detector.analyze(
        text="Status update on Project-X status reports.",
        org_id="test_org",
        custom_patterns=custom_patterns
    )
    assert result_org.has_pii is True
    assert "[INTERNAL_CODENAME]" in result_org.redacted_text
    assert result_org.entities[0].source == "custom"

    # 4. Test Overlap Resolution
    # "john.doe@example.com" could trigger PERSON ("john.doe") and EMAIL ("john.doe@example.com")
    result_overlap = await detector.analyze(
        text="Please contact john.doe@example.com for info.",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result_overlap.has_pii is True
    # Make sure we don't have overlapping replacements that corrupt the output
    assert result_overlap.redacted_text == "Please contact [EMAIL] for info."
