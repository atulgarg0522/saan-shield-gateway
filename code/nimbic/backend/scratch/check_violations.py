import asyncio
import sys
import os
from sqlalchemy import text

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import async_session_local

async def main():
    async with async_session_local() as db:
        # Check all API keys
        res = await db.execute(text("SELECT id, org_id, name, key_prefix, key_hash FROM api_keys"))
        keys = res.fetchall()
        print("--- API Keys ---")
        for k in keys:
            print(f"ID: {k[0]}, Org: {k[1]}, Name: {k[2]}, Prefix: {k[3]}, Hash: {k[4][:15]}...")

        # Check organizations
        res = await db.execute(text("SELECT id, name, slug, plan FROM organizations"))
        orgs = res.fetchall()
        print("\n--- Organizations ---")
        for o in orgs:
            print(f"ID: {o[0]}, Name: {o[1]}, Slug: {o[2]}, Plan: {o[3]}")

        # Check policies
        res = await db.execute(text("SELECT id, org_id, is_active, pii_action, code_action, sensitive_action FROM security_policies"))
        policies = res.fetchall()
        print("\n--- Security Policies ---")
        for p in policies:
            print(f"ID: {p[0]}, Org: {p[1]}, Active: {p[2]}, PII: {p[3]}, Code: {p[4]}, Sens: {p[5]}")

        # Check recent violations
        res = await db.execute(text("SELECT id, org_id, request_id, violation_type, severity, action_taken, created_at FROM security_violations ORDER BY created_at DESC LIMIT 5"))
        violations = res.fetchall()
        print("\n--- Recent Violations ---")
        for v in violations:
            print(f"ID: {v[0]}, Org: {v[1]}, Req: {v[2]}, Type: {v[3]}, Severity: {v[4]}, Action: {v[5]}, Created: {v[6]}")

        # Check recent request logs
        res = await db.execute(text("SELECT id, org_id, request_id, provider, model, status_code, error_message, created_at FROM requests_log ORDER BY created_at DESC LIMIT 15"))
        req_logs = res.fetchall()
        print("\n--- Recent Request Logs ---")
        for rl in req_logs:
            print(f"ID: {rl[0]}, Org: {rl[1]}, Req: {rl[2]}, Provider: {rl[3]}, Model: {rl[4]}, Status: {rl[5]}, Err: {rl[6]}, Created: {rl[7]}")

        res = await db.execute(text("SELECT COUNT(*), provider FROM requests_log GROUP BY provider"))
        for row in res.fetchall():
            print(f"Total for {row[1]}: {row[0]}")

if __name__ == "__main__":
    asyncio.run(main())
