import os
import sys
import asyncio
import random
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete

# Ensure backend folder is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..")))          # Docker container layout (/app)
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..", "backend"))) # Host monorepo layout

from app.db.session import async_session_local
from app.models.org import Organization, OrganizationPlan
from app.models.finops import Team, Project, Budget, BudgetAlert, BudgetScopeEnum, BudgetPeriodEnum, BudgetAlertTypeEnum
from app.models.request_log import RequestLog, ProviderEnum

async def seed_finops():
    print("Starting AI FinOps demo data seeding...")
    async with async_session_local() as db:
        # 1. Resolve Demo Org
        stmt = select(Organization).where(Organization.slug == "demo")
        result = await db.execute(stmt)
        org = result.scalars().first()
        if not org:
            print("Demo Organization not found! Please run 'make seed' first.")
            return

        # Clean existing FinOps data to prevent constraint violations/duplications
        print("Clearing old FinOps records...")
        await db.execute(delete(BudgetAlert).where(BudgetAlert.org_id == org.id))
        await db.execute(delete(Budget).where(Budget.org_id == org.id))
        await db.execute(delete(Project).where(Project.org_id == org.id))
        await db.execute(delete(Team).where(Team.org_id == org.id))
        
        # Also clean logs for the demo to keep it crisp
        await db.execute(delete(RequestLog).where(RequestLog.org_id == org.id))
        await db.commit()

        # 2. Seed Teams (5 Teams)
        print("Creating Teams...")
        team_configs = [
            {"name": "Engineering", "department": "Engineering", "limit": 800.0, "alert_pct": 80},
            {"name": "Marketing", "department": "Marketing", "limit": 300.0, "alert_pct": 75},
            {"name": "Customer Support", "department": "Customer Support", "limit": 400.0, "alert_pct": 90},
            {"name": "HR", "department": "HR", "limit": 100.0, "alert_pct": 85},
            {"name": "Product", "department": "Product", "limit": 200.0, "alert_pct": 80},
        ]
        
        teams = {}
        for tc in team_configs:
            team = Team(
                org_id=org.id,
                name=tc["name"],
                department=tc["department"],
                budget_limit_usd=Decimal(str(tc["limit"])),
                budget_alert_pct=tc["alert_pct"]
            )
            db.add(team)
            await db.commit()
            await db.refresh(team)
            teams[tc["name"]] = team

        # 3. Seed Projects (8 Projects across teams)
        print("Creating Projects...")
        project_configs = [
            {"name": "AI Code Assistant", "team": "Engineering", "limit": 400.0, "alert_pct": 80},
            {"name": "Data Pipeline Bot", "team": "Engineering", "limit": 300.0, "alert_pct": 80},
            {"name": "Content Generator", "team": "Marketing", "limit": 200.0, "alert_pct": 75},
            {"name": "SEO Tool", "team": "Marketing", "limit": 100.0, "alert_pct": 75},
            {"name": "Support Chatbot", "team": "Customer Support", "limit": 350.0, "alert_pct": 90},
            {"name": "Policy Q&A Bot", "team": "HR", "limit": 80.0, "alert_pct": 85},
            {"name": "Feature Analyzer", "team": "Product", "limit": 150.0, "alert_pct": 80},
            {"name": "User Research", "team": "Product", "limit": 100.0, "alert_pct": 80},
        ]

        projects = {}
        for pc in project_configs:
            team = teams[pc["team"]]
            proj = Project(
                org_id=org.id,
                team_id=team.id,
                name=pc["name"],
                budget_limit_usd=Decimal(str(pc["limit"])),
                budget_alert_pct=pc["alert_pct"],
                is_active=True
            )
            db.add(proj)
            await db.commit()
            await db.refresh(proj)
            projects[pc["name"]] = proj

        # 4. Seed Budgets (Budgets table configuration)
        print("Configuring Budgets...")
        # Org Budget
        org_budget = Budget(
            org_id=org.id,
            scope_type=BudgetScopeEnum.org,
            scope_id=org.id,
            period=BudgetPeriodEnum.monthly,
            limit_usd=Decimal("2000.00"),
            alert_pct=80,
            hard_limit=False
        )
        db.add(org_budget)
        
        # Team Budgets
        team_budgets = {}
        for tc in team_configs:
            t_obj = teams[tc["name"]]
            hard_limit_val = (tc["name"] in ["Marketing", "HR"]) # Hard limits for demo contrast
            b_obj = Budget(
                org_id=org.id,
                scope_type=BudgetScopeEnum.team,
                scope_id=t_obj.id,
                period=BudgetPeriodEnum.monthly,
                limit_usd=Decimal(str(tc["limit"])),
                alert_pct=tc["alert_pct"],
                hard_limit=hard_limit_val
            )
            db.add(b_obj)
            team_budgets[tc["name"]] = b_obj

        await db.commit()
        await db.refresh(org_budget)
        for name in team_budgets:
            await db.refresh(team_budgets[name])

        # 5. Pre-generate Budget Alerts
        print("Seeding Budget Alerts...")
        # Engineering soft warnings
        alert_eng_1 = BudgetAlert(
            budget_id=team_budgets["Engineering"].id,
            org_id=org.id,
            alert_type=BudgetAlertTypeEnum.soft_warning,
            usage_pct=Decimal("82.50"),
            usage_usd=Decimal("660.00"),
            limit_usd=Decimal("800.00"),
            created_at=datetime.now(timezone.utc) - timedelta(days=3)
        )
        alert_eng_2 = BudgetAlert(
            budget_id=team_budgets["Engineering"].id,
            org_id=org.id,
            alert_type=BudgetAlertTypeEnum.soft_warning,
            usage_pct=Decimal("91.20"),
            usage_usd=Decimal("729.60"),
            limit_usd=Decimal("800.00"),
            created_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
        
        # Marketing soft warning
        alert_mkt = BudgetAlert(
            budget_id=team_budgets["Marketing"].id,
            org_id=org.id,
            alert_type=BudgetAlertTypeEnum.soft_warning,
            usage_pct=Decimal("76.50"),
            usage_usd=Decimal("229.50"),
            limit_usd=Decimal("300.00"),
            created_at=datetime.now(timezone.utc) - timedelta(days=5)
        )

        db.add_all([alert_eng_1, alert_eng_2, alert_mkt])
        await db.commit()

        # 6. Seed 60 Days of Daily Spend History
        print("Generating 60 days of telemetry trace logs...")
        
        # Target month spend distributions (adding up to target spends for current month)
        # Engineering -> $752 (94% of $800)
        # Marketing -> $234 (78% of $300)
        # Support -> $200 (50% of $400)
        # HR -> $50 (50% of $100)
        # Product -> $80 (40% of $200)
        # Total Current Month Spend: ~$1,316
        
        # Previous month spend distributions (days 31-60 ago) - slightly lower to show trend
        # Eng: $700, Mkt: $210, Support: $180, HR: $40, Product: $70 (Total: $1,200)

        models_pool = [
            {"name": "gpt-4o-mini", "provider": ProviderEnum.openai, "cost_per_req": 0.0015},
            {"name": "claude-3-5-sonnet", "provider": ProviderEnum.anthropic, "cost_per_req": 0.0125},
            {"name": "claude-3-opus", "provider": ProviderEnum.anthropic, "cost_per_req": 0.0850},
        ]

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Loop over the past 60 days
        for day_offset in range(59, -1, -1):
            day_time = now - timedelta(days=day_offset)
            is_current_month = (day_offset < 30)

            # Determine day-based budget shares
            if is_current_month:
                day_shares = {
                    "Engineering": 752.0 / 30.0,
                    "Marketing": 234.0 / 30.0,
                    "Customer Support": 200.0 / 30.0,
                    "HR": 50.0 / 30.0,
                    "Product": 80.0 / 30.0,
                }
            else:
                day_shares = {
                    "Engineering": 700.0 / 30.0,
                    "Marketing": 210.0 / 30.0,
                    "Customer Support": 180.0 / 30.0,
                    "HR": 40.0 / 30.0,
                    "Product": 70.0 / 30.0,
                }

            # Map teams to their projects for distribution
            team_projects = {
                "Engineering": ["AI Code Assistant", "Data Pipeline Bot"],
                "Marketing": ["Content Generator", "SEO Tool"],
                "Customer Support": ["Support Chatbot"],
                "HR": ["Policy Q&A Bot"],
                "Product": ["Feature Analyzer", "User Research"],
            }

            for team_name, target_cost in day_shares.items():
                t_obj = teams[team_name]
                proj_names = team_projects[team_name]
                
                # Add a bit of daily variance (+/- 15%)
                daily_variance = random.uniform(0.85, 1.15)
                actual_cost_to_generate = target_cost * daily_variance
                
                accumulated_cost = 0.0
                req_index = 0
                
                while accumulated_cost < actual_cost_to_generate:
                    # Choose project
                    p_name = random.choice(proj_names)
                    p_obj = projects[p_name]
                    
                    # Choose model
                    # Engineering uses a lot of sonnet/opus, Marketing uses sonnet, Support/HR uses mini/sonnet
                    if team_name == "Engineering":
                        model_choice = random.choices(models_pool, weights=[0.3, 0.5, 0.2])[0]
                    elif team_name == "Marketing":
                        model_choice = random.choices(models_pool, weights=[0.2, 0.7, 0.1])[0]
                    else:
                        model_choice = random.choices(models_pool, weights=[0.7, 0.3, 0.0])[0]

                    base_cost = model_choice["cost_per_req"]
                    req_cost = base_cost * random.uniform(0.8, 1.2)
                    
                    p_tokens = int(req_cost * 100000)
                    c_tokens = int(req_cost * 50000)
                    # Log entry
                    log = RequestLog(
                        org_id=org.id,
                        team_id=t_obj.id,
                        project_id=p_obj.id,
                        department=t_obj.department,
                        request_id=f"req_seed_{day_offset}_{team_name[:3]}_{req_index}",
                        provider=model_choice["provider"],
                        model=model_choice["name"],
                        prompt_tokens=p_tokens,
                        completion_tokens=c_tokens,
                        total_tokens=p_tokens + c_tokens,
                        cost_usd=Decimal(f"{req_cost:.6f}"),
                        latency_ms=random.randint(150, 800),
                        status_code=200,
                        user_identifier=f"user_{random.randint(1, 10)}@saan.ai",
                        created_at=day_time + timedelta(minutes=random.randint(0, 1400))
                    )
                    db.add(log)
                    accumulated_cost += req_cost
                    req_index += 1

            # Commit periodically to keep transaction sizes reasonable
            if day_offset % 5 == 0:
                await db.commit()
                print(f"Seeded day offset {day_offset}...")

        await db.commit()
        print("FinOps demo data seeding completed successfully!")

if __name__ == "__main__":
    asyncio.run(seed_finops())
