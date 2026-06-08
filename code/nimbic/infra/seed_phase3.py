import os
import sys
import asyncio
import uuid
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from sqlalchemy import select

# Set up paths to import from backend
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..")))
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..", "backend")))

from app.db.session import async_session_local
from app.models.org import Organization, OrganizationPlan
from app.models.request_log import RequestLog, ProviderEnum
from app.models.routing_cache import RoutingRule, OrgFAQCache, CostSavingsLog, CostSavingsSource, PromptEmbedding
from app.models.ab_test import ABTest, ABTestResult
from app.routing.semantic_cache import SemanticCache

# Standard FAQs to seed
FAQS = [
    # HR
    {"question": "What is the leave policy?", "answer": "SaaN Shield offers 20 paid vacation days and 10 sick leaves annually, accrued monthly.", "category": "HR"},
    {"question": "How do I apply for reimbursement?", "answer": "Submit all receipts on Expensify under the 'Work Expense' category for manager approval.", "category": "HR"},
    {"question": "When is the monthly payroll processed?", "answer": "Payroll is processed and direct deposits are initiated on the 25th of each calendar month.", "category": "HR"},
    {"question": "What are the core working hours?", "answer": "Core collaborative hours are 10:00 AM to 4:00 PM EST, monday through Friday.", "category": "HR"},
    {"question": "How do I update my tax withholding?", "answer": "Log in to Workday, navigate to Pay > Tax Withholding, and fill out a new W-4 form.", "category": "HR"},
    # IT
    {"question": "How do I reset my VPN?", "answer": "Go to portal.vpn.com, enter your corporate email, click 'Forgot Password' and follow the setup verification instructions.", "category": "IT"},
    {"question": "What is the wifi password for the office?", "answer": "The secure wifi password for the office is 'SaaNShield2026!' on the SSID 'SaaN-HQ'.", "category": "IT"},
    {"question": "Who do I contact for computer issues?", "answer": "Submit a technical ticket in Jira Service Desk or email it-helpdesk@saan-shield.com.", "category": "IT"},
    {"question": "How do I request a software license?", "answer": "Navigate to the IT portal, select 'Request Software License', choose the application and submit for manager approval.", "category": "IT"},
    {"question": "What is the policy for multi-factor authentication?", "answer": "Multi-factor authentication (MFA) is mandatory on all corporate credentials via Okta Verify.", "category": "IT"},
    # Policy
    {"question": "What is the data retention policy?", "answer": "SaaN Shield AI Gateway retains standard query logs and metadata for exactly 90 days before automated purging.", "category": "Policy"},
    {"question": "Who do I contact for compliance questions?", "answer": "Direct all compliance and security questions to the Chief Compliance Officer at compliance@saan-shield.com.", "category": "Policy"},
    {"question": "What is the clean desk policy?", "answer": "Secure all sensitive physical documents in locked drawers and lock your computer screen before leaving your workspace.", "category": "Policy"},
    {"question": "What is the remote work policy?", "answer": "Employees can work remotely up to 3 days per week with manager approval, subject to role alignment.", "category": "Policy"},
    {"question": "Is sharing credentials permitted?", "answer": "No, credential sharing is strictly prohibited under the IT security compliance code.", "category": "Policy"}
]


async def seed_phase3():
    print("Starting Phase 3 and A/B Testing database seeding...")
    async with async_session_local() as db:
        # 1. Fetch Demo Organization
        stmt = select(Organization).where(Organization.slug == "demo")
        result = await db.execute(stmt)
        org = result.scalars().first()
        if not org:
            print("ERROR: Demo organization not found. Run 'make seed' first.")
            return
        
        org_id = org.id
        print(f"Targeting organization: {org.name} ({org_id})")

        # 2. Seed Routing Rules
        print("Seeding Routing Rules...")
        rules_payload = [
            {
                "name": "Chat to Haiku",
                "conditions": {"category": "chat"},
                "target_model": "claude-haiku-4-5",
                "target_provider": "anthropic",
                "priority": 1
            },
            {
                "name": "Code to Sonnet",
                "conditions": {"category": "coding"},
                "target_model": "claude-sonnet-4-6",
                "target_provider": "anthropic",
                "priority": 2
            },
            {
                "name": "Large prompts to Opus",
                "conditions": {"min_tokens": 2000},
                "target_model": "claude-opus-4-6",
                "target_provider": "anthropic",
                "priority": 3
            }
        ]

        for rule_data in rules_payload:
            # Check if exists
            stmt_r = select(RoutingRule).where(
                RoutingRule.org_id == org_id,
                RoutingRule.name == rule_data["name"]
            )
            existing = (await db.execute(stmt_r)).scalars().first()
            if not existing:
                rule = RoutingRule(
                    org_id=org_id,
                    name=rule_data["name"],
                    conditions=rule_data["conditions"],
                    target_model=rule_data["target_model"],
                    target_provider=rule_data["target_provider"],
                    priority=rule_data["priority"],
                    is_active=True
                )
                db.add(rule)
        await db.commit()
        print("Routing Rules seeded.")

        # 3. Seed FAQs
        print("Seeding FAQs (this will generate vector embeddings)...")
        # Delete existing FAQs to avoid duplicate seeds
        await db.execute(OrgFAQCache.__table__.delete().where(OrgFAQCache.org_id == org_id))
        await db.commit()

        cache_mgr = SemanticCache()
        inserted_faqs = await cache_mgr.seed_faq(str(org_id), FAQS, db)
        print(f"Successfully seeded {inserted_faqs} FAQ entries.")

        # 4. Seed Prompt Cache Entries (PromptEmbedding)
        print("Seeding some historical PromptEmbeddings...")
        # Clear existing cached prompt embeddings to prevent duplicates
        await db.execute(PromptEmbedding.__table__.delete().where(PromptEmbedding.org_id == org_id))
        await db.commit()

        cache_prompts = [
            ("what is machine learning", "Machine learning is a method of data analysis that automates analytical model building.", "gpt-4o-mini"),
            ("how to build a docker container", "To build a docker container, write a Dockerfile and run: docker build -t image_name .", "gpt-4o-mini"),
            ("list the active routing keys", "Active keys are listed under /api/v1/keys in the gateway manager portal.", "gpt-4o-mini"),
            ("explain cosine similarity", "Cosine similarity measures the similarity between two vectors by calculating the cosine of the angle between them.", "gpt-4o-mini"),
            ("who founded saan shield", "SaaN Shield was developed by the security compliance orchestration division.", "gpt-4o-mini")
        ]

        for prompt_text, resp_text, model_used in cache_prompts:
            await cache_mgr.store(
                prompt=prompt_text,
                response=resp_text,
                model=model_used,
                org_id=str(org_id),
                db=db,
                redis=AsyncMock(), # Dummy Redis client since we just need pgvector seeding
                has_pii=False
            )
        print("Prompt embeddings cache seeded.")

        # 5. Seed 90-day Cost Savings Logs and Requests Logs
        print("Seeding 90 days of transactions (2,000 requests total)...")
        
        # Clear existing logs to prevent demo bloating
        await db.execute(CostSavingsLog.__table__.delete().where(CostSavingsLog.org_id == org_id))
        await db.execute(RequestLog.__table__.delete().where(RequestLog.org_id == org_id))
        await db.commit()

        total_requests = 2000
        days = 90
        requests_per_day = total_requests // days
        
        # Target Distribution:
        # 40% Cache Hits (800) -> actual cost = 0, baseline cost = gpt-4o price (~$0.80-$1.50 range)
        # 35% Haiku/Flash (700) -> actual cost = ~$0.01-$0.05, baseline = ~$0.30-$0.70
        # 20% Sonnet (400) -> actual = ~$0.15-$0.25, baseline = ~$0.30-$0.70
        # 5% Opus (100) -> actual = ~$0.90-$1.20, baseline = ~$0.30-$0.70

        req_count = 0
        now_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # Pre-generate unique request IDs to prevent DB conflicts
        request_ids = [f"req_seed_{uuid.uuid4().hex[:16]}" for _ in range(total_requests)]

        for day in range(days):
            date_target = now_time - timedelta(days=90 - day)
            
            # Slightly vary daily counts to make logs look organic
            daily_count = requests_per_day + random.randint(-5, 5)
            
            for _ in range(daily_count):
                if req_count >= total_requests:
                    break
                
                req_id = request_ids[req_count]
                req_count += 1
                
                # Determine type based on target percentage distributions
                rand_val = random.random()
                
                if rand_val < 0.40:
                    # 1. Cache Hit (40%)
                    actual_provider = "cache"
                    actual_model = "cache_hit"
                    actual_cost = Decimal("0.000000")
                    
                    baseline_model = "gpt-4o"
                    baseline_cost = Decimal(str(round(random.uniform(0.80, 1.50), 6)))
                    savings = baseline_cost
                    source = CostSavingsSource.cache_hit
                    latency = random.randint(5, 45) # fast!
                    prompt_tokens = random.randint(50, 300)
                    completion_tokens = random.randint(200, 1000)
                    status_code = 200
                    
                elif rand_val < 0.75:
                    # 2. Haiku/Flash (35%)
                    actual_provider = "anthropic"
                    actual_model = "claude-haiku-4-5"
                    actual_cost = Decimal(str(round(random.uniform(0.01, 0.05), 6)))
                    
                    baseline_model = "gpt-4o"
                    baseline_cost = Decimal(str(round(random.uniform(0.30, 0.70), 6)))
                    savings = baseline_cost - actual_cost
                    source = CostSavingsSource.model_routing
                    latency = random.randint(200, 500)
                    prompt_tokens = random.randint(150, 500)
                    completion_tokens = random.randint(100, 400)
                    status_code = 200
                    
                elif rand_val < 0.95:
                    # 3. Sonnet (20%)
                    actual_provider = "anthropic"
                    actual_model = "claude-sonnet-4-6"
                    actual_cost = Decimal(str(round(random.uniform(0.15, 0.25), 6)))
                    
                    baseline_model = "gpt-4o"
                    baseline_cost = Decimal(str(round(random.uniform(0.30, 0.70), 6)))
                    savings = baseline_cost - actual_cost
                    source = CostSavingsSource.model_routing
                    latency = random.randint(450, 950)
                    prompt_tokens = random.randint(200, 800)
                    completion_tokens = random.randint(300, 900)
                    status_code = 200
                    
                else:
                    # 4. Opus (5%) - Negative Savings
                    actual_provider = "anthropic"
                    actual_model = "claude-opus-4-6"
                    actual_cost = Decimal(str(round(random.uniform(0.90, 1.20), 6)))
                    
                    baseline_model = "gpt-4o"
                    baseline_cost = Decimal(str(round(random.uniform(0.30, 0.70), 6)))
                    savings = baseline_cost - actual_cost
                    source = CostSavingsSource.model_routing
                    latency = random.randint(1000, 2200)
                    prompt_tokens = random.randint(300, 1200)
                    completion_tokens = random.randint(400, 1500)
                    status_code = 200
                
                # Injects date noise
                date_noise = date_target + timedelta(hours=random.randint(0, 23), minutes=random.randint(0, 59))
                
                # Insert RequestLog (ProviderEnum requires matching value string)
                prov_enum = ProviderEnum(actual_provider) if actual_provider in [p.value for p in ProviderEnum] else ProviderEnum.OPENAI
                
                req_log = RequestLog(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    request_id=req_id,
                    provider=prov_enum,
                    model=actual_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    cost_usd=actual_cost,
                    latency_ms=latency,
                    status_code=status_code,
                    created_at=date_noise
                )
                db.add(req_log)

                # Insert CostSavingsLog
                savings_log = CostSavingsLog(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    request_id=req_id,
                    actual_model=actual_model,
                    actual_cost_usd=actual_cost,
                    baseline_model=baseline_model,
                    baseline_cost_usd=baseline_cost,
                    savings_usd=savings,
                    source=source,
                    created_at=date_noise
                )
                db.add(savings_log)

            # Flush daily records to avoid memory bloating
            if day % 10 == 0:
                await db.flush()
                
        await db.commit()
        print("Historical requests and cost savings seeded successfully.")

        # 6. Seed one active A/B test
        print("Seeding active A/B test ('LLM Cost Optimization Study')...")
        # Clean existing A/B test data first
        await db.execute(ABTestResult.__table__.delete().where(ABTestResult.org_id == org_id))
        await db.execute(ABTest.__table__.delete().where(ABTest.org_id == org_id))
        await db.commit()

        ab_test = ABTest(
            id=uuid.uuid4(),
            org_id=org_id,
            name="LLM Cost Optimization Study",
            model_a="gpt-4o-mini",
            provider_a="openai",
            model_b="claude-haiku-4-5",
            provider_b="anthropic",
            split_pct=20,
            status="active",
            started_at=now_time - timedelta(days=14),
            created_at=now_time - timedelta(days=14)
        )
        db.add(ab_test)
        await db.flush()

        # Seed 500 variant performance logging records:
        # Variant A: 80% (400 records) -> cost ~ 0.000150, latency ~ 400ms
        # Variant B: 20% (100 records) -> cost ~ 0.000105 (30% cheaper), latency ~ 340ms (15% faster)
        print("Seeding 500 A/B test variant result logs (Variant A: 400, Variant B: 100)...")
        
        ab_test_id = ab_test.id
        for i in range(500):
            # 80/20 probability distribution matching the traffic split pct
            is_variant_b = random.random() < 0.20
            
            if is_variant_b:
                variant = "B"
                # Claude-Haiku: 30% cheaper, 15% faster
                cost = Decimal(str(round(random.uniform(0.000090, 0.000115), 6)))
                latency = random.randint(310, 360)
            else:
                variant = "A"
                # GPT-4o-mini: standard baseline
                cost = Decimal(str(round(random.uniform(0.000130, 0.000170), 6)))
                latency = random.randint(375, 420)
                
            test_res = ABTestResult(
                id=uuid.uuid4(),
                test_id=ab_test_id,
                org_id=org_id,
                request_id=f"req_ab_seed_{uuid.uuid4().hex[:12]}",
                variant=variant,
                cost=cost,
                latency=latency,
                created_at=now_time - timedelta(days=random.randint(0, 14), hours=random.randint(0, 23))
            )
            db.add(test_res)
            
        await db.commit()
        print("Active A/B Test seeded successfully.")
        print("\nSeeding Completed Successfully!")


if __name__ == "__main__":
    asyncio.run(seed_phase3())
