import os
import sys
import asyncio
from sqlalchemy import select

# Ensure backend folder is in sys.path (supports both host and docker environment layouts)
current_dir = os.path.dirname(os.path.abspath(__file__))
# Insert at index 0 to prioritize local source tree over site-packages
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..")))          # Docker container layout (/app)
sys.path.insert(0, os.path.abspath(os.path.join(current_dir, "..", "backend"))) # Host monorepo layout (SaaN Shield/backend)

from app.db.session import async_session_local
from app.models.org import Organization, OrganizationPlan
from app.models.request_log import ProviderEnum
from app.models.provider_config import ProviderConfig
from app.services.org_svc import create_org
from app.services.key_svc import create_api_key
from app.services.provider_svc import upsert_provider_config

async def seed():
    print("Starting database seeding...")
    async with async_session_local() as db:
        # Check if org already exists
        stmt = select(Organization).where(Organization.slug == "demo")
        result = await db.execute(stmt)
        org = result.scalars().first()
        
        if not org:
            print("Creating organization 'Demo Org'...")
            org = await create_org(
                name="Demo Org",
                slug="demo",
                plan=OrganizationPlan.FREE,
                db=db
            )
        else:
            print("Organization 'Demo Org' already exists.")
            
        # Create an admin API key
        print("Creating admin API key...")
        api_key_obj, raw_key = await create_api_key(
            org_id=org.id,
            name="Default Admin Key",
            scopes=["proxy", "logs:read", "admin"],
            db=db
        )
        
        print("\n" + "="*60)
        print(f"Your API key: {raw_key} (save this!)")
        print("="*60 + "\n")
        
        # Configure OpenAI provider
        openai_key = os.getenv("OPENAI_API_KEY") or "sk-proj-demo-openai-key-placeholder-value-12345"
        print(f"Upserting OpenAI provider config...")
        await upsert_provider_config(
            org_id=org.id,
            provider=ProviderEnum.OPENAI,
            raw_api_key=openai_key,
            base_url="https://api.openai.com/v1",
            db=db
        )
        
        # Configure Anthropic provider
        anthropic_key = os.getenv("ANTHROPIC_API_KEY") or "sk-ant-demo-anthropic-key-placeholder-value-12345"
        print(f"Upserting Anthropic provider config...")
        await upsert_provider_config(
            org_id=org.id,
            provider=ProviderEnum.ANTHROPIC,
            raw_api_key=anthropic_key,
            base_url="https://api.anthropic.com",
            db=db
        )
        
        print("Seeding completed successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
