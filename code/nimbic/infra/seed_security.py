import os
import sys
import asyncio
import random
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

# Ensure backend folder is in sys.path (supports both host and docker environment layouts)
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..")))          # Docker container layout (/app)
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..", "backend"))) # Host monorepo layout (SaaN Shield/backend)

from app.db.session import async_session_local
from app.models.org import Organization, OrganizationPlan
from app.models.security_policy import SecurityPolicy, PiiActionEnum, PolicyActionEnum
from app.models.security_violation import SecurityViolation, ViolationTypeEnum, SeverityEnum, ViolationActionEnum

# Predefined realistic prompt snippets and matching details for PII (20 items)
pii_templates = [
    # 5 Critical PII (Aadhaar, Private Key, AWS Credentials)
    {
        "snippet": "Here is the production server key -----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...",
        "details": {"entities": [{"type": "PRIVATE_KEY", "score": 0.95, "source": "regex", "start": 33, "end": 64}]},
        "severity": SeverityEnum.critical,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Use AWS credentials: AKIAIOSFODNN7EXAMPLE and secret key to upload files.",
        "details": {"entities": [{"type": "AWS_KEY", "score": 0.95, "source": "regex", "start": 21, "end": 41}]},
        "severity": SeverityEnum.critical,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Can you verify if this Aadhaar is valid? 3201 4592 1023",
        "details": {"entities": [{"type": "AADHAAR", "score": 0.85, "source": "regex", "start": 41, "end": 55}]},
        "severity": SeverityEnum.critical,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "My PAN card number is FGHIJ5678K, please process my application.",
        "details": {"entities": [{"type": "PAN", "score": 0.85, "source": "regex", "start": 22, "end": 32}]},
        "severity": SeverityEnum.critical,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Customer SSN is 000-12-3456, let me know if you need the full file.",
        "details": {"entities": [{"type": "US_SSN", "score": 0.85, "source": "presidio", "start": 16, "end": 27}]},
        "severity": SeverityEnum.critical,
        "action": ViolationActionEnum.blocked
    },
    # 3 High PII (Redacted)
    {
        "snippet": "Contact me at sales-admin@acme-corp.com or call +1 555-0199.",
        "details": {"entities": [{"type": "EMAIL", "score": 0.9, "source": "presidio", "start": 14, "end": 38}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Please send the report to chief-executive@startup.io as soon as possible.",
        "details": {"entities": [{"type": "EMAIL", "score": 0.9, "source": "presidio", "start": 26, "end": 52}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "My personal phone number is 9876543210, call me after 5 PM.",
        "details": {"entities": [{"type": "PHONE_NUMBER", "score": 0.8, "source": "regex", "start": 28, "end": 38}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.redacted
    },
    # 8 Medium PII (Redacted)
    {
        "snippet": "Send UPI transfer to billing@okaxis for the pending invoices.",
        "details": {"entities": [{"type": "UPI_ID", "score": 0.85, "source": "regex", "start": 21, "end": 35}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Our bank IFSC is UTIB0000244 for wire transfers.",
        "details": {"entities": [{"type": "IFSC", "score": 0.85, "source": "regex", "start": 17, "end": 28}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Verification details: email at test-user@outlook.com, phone 8765432109.",
        "details": {"entities": [{"type": "EMAIL", "score": 0.9, "source": "presidio", "start": 30, "end": 51}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Support ticket raised for developer@github-enterprise.co.",
        "details": {"entities": [{"type": "EMAIL", "score": 0.9, "source": "presidio", "start": 27, "end": 56}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Send payments to alex.smith@okicici and notify immediately.",
        "details": {"entities": [{"type": "UPI_ID", "score": 0.85, "source": "regex", "start": 17, "end": 34}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Recipient IP address is 192.168.1.150.",
        "details": {"entities": [{"type": "IP_ADDRESS", "score": 0.85, "source": "presidio", "start": 24, "end": 37}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "My phone is 9988776655, ping me on WhatsApp.",
        "details": {"entities": [{"type": "PHONE_NUMBER", "score": 0.8, "source": "regex", "start": 12, "end": 22}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Send details to hr-benefits@workday-partner.com for pension scheme.",
        "details": {"entities": [{"type": "EMAIL", "score": 0.9, "source": "presidio", "start": 16, "end": 47}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    # 4 Low PII (Allowed)
    {
        "snippet": "Draft an email response to Dr. Marcus Vance regarding the schedule.",
        "details": {"entities": [{"type": "PERSON", "score": 0.7, "source": "presidio", "start": 27, "end": 43}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.allowed
    },
    {
        "snippet": "Schedule a meeting with Atul Sharma next Monday.",
        "details": {"entities": [{"type": "PERSON", "score": 0.75, "source": "presidio", "start": 24, "end": 35}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.allowed
    },
    {
        "snippet": "Provide directions to the office in Bengaluru, India.",
        "details": {"entities": [{"type": "LOCATION", "score": 0.65, "source": "presidio", "start": 36, "end": 52}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.allowed
    },
    {
        "snippet": "Send invite to Sarah Connor for the upcoming project kickoff.",
        "details": {"entities": [{"type": "PERSON", "score": 0.75, "source": "presidio", "start": 15, "end": 27}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.allowed
    }
]

# Source Code (15 items)
code_templates = [
    # 5 High Severity Code (DANGEROUS SQL, DROP/DELETE/TRUNCATE)
    {
        "snippet": "Execute this command on the cluster:\nDROP TABLE customer_records CASCADE;",
        "details": {"languages": ["sql"], "snippets": [{"language": "sql", "snippet": "DROP TABLE customer_records CASCADE;", "start_line": 2, "indicator_type": "sql_query"}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Clear the sandbox database using TRUNCATE TABLE logs_archive;",
        "details": {"languages": ["sql"], "snippets": [{"language": "sql", "snippet": "TRUNCATE TABLE logs_archive;", "start_line": 1, "indicator_type": "sql_query"}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Query to clear duplicates:\nDELETE FROM payments WHERE status = 'failed';",
        "details": {"languages": ["sql"], "snippets": [{"language": "sql", "snippet": "DELETE FROM payments WHERE status = 'failed';", "start_line": 2, "indicator_type": "sql_query"}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Setup script shebang:\n#!/bin/bash\nchmod 777 /opt/secrets/\nrm -rf /var/tmp/*",
        "details": {"languages": ["shell"], "snippets": [{"language": "shell", "snippet": "#!/bin/bash", "start_line": 2, "indicator_type": "shebang"}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Remove all users:\nDROP TABLE users; -- wipe it out",
        "details": {"languages": ["sql"], "snippets": [{"language": "sql", "snippet": "DROP TABLE users;", "start_line": 2, "indicator_type": "sql_query"}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.blocked
    },
    # 5 Medium Severity Code (Python/JS functions, SQL Selects)
    {
        "snippet": "Check if this JS works:\nconst calculateTotal = (prices) => {\n  return prices.reduce((a, b) => a + b, 0);\n};",
        "details": {"languages": ["javascript"], "snippets": [{"language": "javascript", "snippet": "const calculateTotal = (prices) => {", "start_line": 2, "indicator_type": "function_def"}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Translate this SQL query to Pandas:\nSELECT email, role FROM admin_users WHERE active = true LIMIT 10;",
        "details": {"languages": ["sql"], "snippets": [{"language": "sql", "snippet": "SELECT email, role FROM admin_users WHERE active = true LIMIT 10;", "start_line": 2, "indicator_type": "sql_query"}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Optimize this Python function:\ndef fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)",
        "details": {"languages": ["python"], "snippets": [{"language": "python", "snippet": "def fib(n):", "start_line": 2, "indicator_type": "function_def"}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Here is the module exports setup:\nmodule.exports = { connectDb, disconnectDb };",
        "details": {"languages": ["javascript"], "snippets": [{"language": "javascript", "snippet": "module.exports = { connectDb, disconnectDb };", "start_line": 2, "indicator_type": "import"}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Importing models:\nfrom app.models.org import Organization\nimport os",
        "details": {"languages": ["python"], "snippets": [{"language": "python", "snippet": "from app.models.org import Organization", "start_line": 2, "indicator_type": "import"}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.warned
    },
    # 5 Low Severity Code
    {
        "snippet": "Check this class configuration:\npublic class DatabaseConnectionManager {\n   private Connection conn;\n}",
        "details": {"languages": ["java"], "snippets": [{"language": "java", "snippet": "public class DatabaseConnectionManager {", "start_line": 2, "indicator_type": "function_def"}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Here is my deployment script:\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: gateway",
        "details": {"languages": ["yaml"], "snippets": [{"language": "yaml", "snippet": "apiVersion: apps/v1", "start_line": 2, "indicator_type": "import"}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "How do I run a command inside a Docker container? e.g. sudo docker ps",
        "details": {"languages": ["shell"], "snippets": [{"language": "shell", "snippet": "sudo docker ps", "start_line": 1, "indicator_type": "function_def"}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Write an override function in Kotlin with @Override annotation.",
        "details": {"languages": ["java"], "snippets": [{"language": "java", "snippet": "@Override", "start_line": 1, "indicator_type": "function_def"}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Print out text using echo command in bash:\necho \"Process complete.\"",
        "details": {"languages": ["shell"], "snippets": [{"language": "shell", "snippet": "echo \"Process complete.\"", "start_line": 2, "indicator_type": "function_def"}]},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.warned
    }
]

# Sensitive Content (10 items)
sensitive_templates = [
    # 5 High Severity Sensitive Content (Roadmaps, NDA settlements, M&A)
    {
        "snippet": "Please summarize our strategic roadmap Q3 and go-to-market plan.",
        "details": {"confidence": 0.85, "categories": [{"name": "strategic", "matched_words": ["roadmap Q", "go-to-market"], "confidence": 0.85}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Draft a competitive intelligence outline of the upcoming merger and acquisition details.",
        "details": {"confidence": 0.9, "categories": [{"name": "strategic", "matched_words": ["competitive intelligence", "merger", "acquisition"], "confidence": 0.9}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Here is the confidential settlement draft for the ongoing legal litigation.",
        "details": {"confidence": 0.85, "categories": [{"name": "legal", "matched_words": ["confidential settlement", "litigation"], "confidence": 0.85}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "What are the core clauses of this Non-Disclosure Agreement (NDA)?",
        "details": {"confidence": 0.9, "categories": [{"name": "legal", "matched_words": ["NDA", "Non-disclosure"], "confidence": 0.9}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.warned
    },
    {
        "snippet": "Write an update summary regarding employee compensation adjustments and headcount metrics.",
        "details": {"confidence": 0.85, "categories": [{"name": "hr", "matched_words": ["compensation", "headcount"], "confidence": 0.85}]},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.redacted
    },
    # 5 Medium Severity Sensitive Content
    {
        "snippet": "Check if our EBITDA and runway figures are ready for investor slides.",
        "details": {"confidence": 0.75, "categories": [{"name": "financial", "matched_words": ["EBITDA", "runway"], "confidence": 0.75}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.redacted
    },
    {
        "snippet": "Provide a calculation sheet for quarterly results and valuation multiples.",
        "details": {"confidence": 0.7, "categories": [{"name": "financial", "matched_words": ["quarterly results", "valuation"], "confidence": 0.7}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.allowed
    },
    {
        "snippet": "We need to plan a competitive intelligence campaign for pricing strategy.",
        "details": {"confidence": 0.65, "categories": [{"name": "strategic", "matched_words": ["competitive intelligence", "pricing strategy"], "confidence": 0.65}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.allowed
    },
    {
        "snippet": "Draft standard termination policy and PIP guidelines.",
        "details": {"confidence": 0.7, "categories": [{"name": "hr", "matched_words": ["termination", "PIP"], "confidence": 0.7}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.allowed
    },
    {
        "snippet": "Is the patient history and medical record compliance standard under HIPAA?",
        "details": {"confidence": 0.75, "categories": [{"name": "medical", "matched_words": ["medical history", "HIPAA", "patient record"], "confidence": 0.75}]},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.allowed
    }
]

# Residency (5 items)
residency_templates = [
    # 2 High Severity Blocked (Blocked region CN, RU)
    {
        "snippet": "Hello, translate this paragraph.",
        "details": {"client_ip": "114.240.0.1", "country": "CN", "region": "AS", "provider": "openai", "reason": "Data Residency Policy: Country code 'CN' is blocked.", "suggested_provider": None},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Draft a message response to support.",
        "details": {"client_ip": "85.143.0.1", "country": "RU", "region": "EU", "provider": "openai", "reason": "Data Residency Policy: Country code 'RU' is blocked.", "suggested_provider": None},
        "severity": SeverityEnum.high,
        "action": ViolationActionEnum.blocked
    },
    # 2 Medium Severity (EU provider redirects)
    {
        "snippet": "Analyze this data set.",
        "details": {"client_ip": "5.5.5.5", "country": "FR", "region": "EU", "provider": "openai", "reason": "Data Residency Policy: provider 'openai' is not allowed for your region. Use 'azure_openai' instead.", "suggested_provider": "azure_openai"},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.blocked
    },
    {
        "snippet": "Write an executive summary.",
        "details": {"client_ip": "62.4.0.1", "country": "DE", "region": "EU", "provider": "anthropic", "reason": "Data Residency Policy: provider 'anthropic' is not allowed for your region. Use 'azure_openai' instead.", "suggested_provider": "azure_openai"},
        "severity": SeverityEnum.medium,
        "action": ViolationActionEnum.allowed
    },
    # 1 Low Severity
    {
        "snippet": "Summarize this ticket.",
        "details": {"client_ip": "82.165.0.1", "country": "DE", "region": "EU", "provider": "gemini", "reason": "Data Residency Policy: provider 'gemini' is not allowed for your region. Use 'azure_openai' instead.", "suggested_provider": "azure_openai"},
        "severity": SeverityEnum.low,
        "action": ViolationActionEnum.allowed
    }
]

async def seed_security():
    print("Starting database seeding for security dashboard...")
    async with async_session_local() as db:
        # 1. Fetch or create organization 'Demo Org' (slug = 'demo')
        stmt = select(Organization).where(Organization.slug == "demo")
        result = await db.execute(stmt)
        org = result.scalars().first()
        
        if not org:
            print("Creating organization 'Demo Org'...")
            org = Organization(
                name="Demo Org",
                slug="demo",
                plan=OrganizationPlan.PRO
            )
            db.add(org)
            await db.commit()
            await db.refresh(org)
        else:
            print("Organization 'Demo Org' loaded.")

        # 2. Setup Security Policy
        print("Upserting security policy for demo organization...")
        stmt_p = select(SecurityPolicy).where(SecurityPolicy.org_id == org.id)
        result_p = await db.execute(stmt_p)
        policy = result_p.scalars().first()

        if not policy:
            policy = SecurityPolicy(
                org_id=org.id,
                pii_action=PiiActionEnum.redact,
                code_action=PolicyActionEnum.warn,
                sensitive_action=PolicyActionEnum.warn,
                blocked_regions=["CN", "RU", "KP"],
                allowed_providers_by_region={},
                custom_patterns=[],
                is_active=True
            )
            db.add(policy)
        else:
            policy.pii_action = PiiActionEnum.redact
            policy.code_action = PolicyActionEnum.warn
            policy.sensitive_action = PolicyActionEnum.warn
            policy.blocked_regions = ["CN", "RU", "KP"]
            policy.is_active = True
            db.add(policy)

        await db.commit()
        print(f"Policy configured: PII=redact, Code=warn, Sensitive=warn, Blocked regions={policy.blocked_regions}")

        # Clear existing violations for the demo org to avoid accumulation
        from sqlalchemy import delete
        await db.execute(delete(SecurityViolation).where(SecurityViolation.org_id == org.id))
        await db.commit()
        print("Purged old violations for demo organization.")

        # 3. Create 50 violations spread over the last 14 days
        all_violations = []

        # PII templates (20 items)
        for idx, t in enumerate(pii_templates):
            all_violations.append((ViolationTypeEnum.pii, t["severity"], t["action"], t["snippet"], t["details"], idx))

        # Code templates (15 items)
        for idx, t in enumerate(code_templates):
            all_violations.append((ViolationTypeEnum.source_code, t["severity"], t["action"], t["snippet"], t["details"], idx))

        # Sensitive templates (10 items)
        for idx, t in enumerate(sensitive_templates):
            all_violations.append((ViolationTypeEnum.sensitive_content, t["severity"], t["action"], t["snippet"], t["details"], idx))

        # Residency templates (5 items)
        for idx, t in enumerate(residency_templates):
            all_violations.append((ViolationTypeEnum.data_residency, t["severity"], t["action"], t["snippet"], t["details"], idx))

        # Verify totals
        assert len(all_violations) == 50, f"Violations count must be exactly 50, got {len(all_violations)}"

        # Verify severity distribution
        sevs = [v[1] for v in all_violations]
        assert sevs.count(SeverityEnum.critical) == 5, f"Expected 5 critical, got {sevs.count(SeverityEnum.critical)}"
        assert sevs.count(SeverityEnum.high) == 15, f"Expected 15 high, got {sevs.count(SeverityEnum.high)}"
        assert sevs.count(SeverityEnum.medium) == 20, f"Expected 20 medium, got {sevs.count(SeverityEnum.medium)}"
        assert sevs.count(SeverityEnum.low) == 10, f"Expected 10 low, got {sevs.count(SeverityEnum.low)}"

        # Verify action distribution
        actions = [v[2] for v in all_violations]
        assert actions.count(ViolationActionEnum.blocked) == 10, f"Expected 10 blocked, got {actions.count(ViolationActionEnum.blocked)}"
        assert actions.count(ViolationActionEnum.redacted) == 15, f"Expected 15 redacted, got {actions.count(ViolationActionEnum.redacted)}"
        assert actions.count(ViolationActionEnum.warned) == 15, f"Expected 15 warned, got {actions.count(ViolationActionEnum.warned)}"
        assert actions.count(ViolationActionEnum.allowed) == 10, f"Expected 10 allowed, got {actions.count(ViolationActionEnum.allowed)}"

        print("Distributions verified. Populating to database...")

        # We will spread them randomly across the last 14 days
        random.seed(42)  # For deterministic dates generation
        start_date = datetime.now() - timedelta(days=14)

        for v_type, v_sev, v_act, v_snippet, v_details, idx in all_violations:
            random_hours = random.randint(1, 14 * 24)
            created_at = start_date + timedelta(hours=random_hours)
            request_id = f"req-demo-sec-{v_type.value[:3]}-{idx}-{random.randint(1000, 9999)}"

            violation_db = SecurityViolation(
                org_id=org.id,
                request_id=request_id,
                violation_type=v_type,
                severity=v_sev,
                action_taken=v_act,
                details=v_details,
                prompt_snippet=v_snippet,
                created_at=created_at
            )
            db.add(violation_db)

        await db.commit()
        print("Database seeded with exactly 50 realistic security violations successfully!")

if __name__ == "__main__":
    asyncio.run(seed_security())
