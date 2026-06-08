import pytest
from app.security.code_detector import CodeDetector
from app.security.sensitivity_classifier import SensitivityClassifier


@pytest.mark.asyncio
async def test_code_detector_signatures():
    detector = CodeDetector()

    # 1. Python Snippet
    py_text = "def hello_world():\n    import sys\n    print('hello')"
    py_res = await detector.analyze(py_text)
    assert py_res.has_code is True
    assert "python" in py_res.languages
    # 2 out of 3 lines match -> high severity
    assert py_res.severity == "high"
    assert any(s.indicator_type == "function_def" for s in py_res.snippets)
    assert any(s.indicator_type == "import" for s in py_res.snippets)

    # 2. Dangerous SQL override
    sql_text = "Let's DROP TABLE customers and see what happens."
    sql_res = await detector.analyze(sql_text)
    assert sql_res.has_code is True
    assert "sql" in sql_res.languages
    # Dangerous commands like DROP override to high severity immediately
    assert sql_res.severity == "high"

    # 3. Incidental/No code
    normal_text = "Hello, can you help me write an essay on historical events?"
    normal_res = await detector.analyze(normal_text)
    assert normal_res.has_code is False
    assert normal_res.severity == "low"


@pytest.mark.asyncio
async def test_sensitivity_classifier():
    classifier = SensitivityClassifier()

    # 1. Financial + Strategic with Boost
    sensitive_text = (
        "CONFIDENTIAL: Our strategic goal is the acquisition of Acme Corporation. "
        "This will increase our revenue and quarterly EBITDA results."
    )
    res = await classifier.analyze(sensitive_text)
    assert res.is_sensitive is True
    assert len(res.categories) >= 2
    # Ensure "financial" and "strategic" are detected
    categories = [cat.name for cat in res.categories]
    assert "financial" in categories
    assert "strategic" in categories
    # High confidence because of boost + multiple matches
    assert res.severity in ("high", "medium")

    # 2. Critical Credentials Match
    cred_text = "Access is restricted. My password is super-secure-pass-123"
    cred_res = await classifier.analyze(cred_text)
    assert cred_res.is_sensitive is True
    assert any(cat.name == "auth_credentials" for cat in cred_res.categories)
    # Auth credentials always carry critical severity
    assert cred_res.severity == "critical"

    # 3. Normal / Non-sensitive
    plain_text = "Let's go for a walk in the park today."
    plain_res = await classifier.analyze(plain_text)
    assert plain_res.is_sensitive is False
    assert plain_res.severity == "low"
