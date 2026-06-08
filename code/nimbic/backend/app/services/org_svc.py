import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.org import Organization, OrganizationPlan


async def get_org_by_id(org_id: uuid.UUID, db: AsyncSession) -> Optional[Organization]:
    """
    Retrieves an organization record from database by its ID.
    """
    stmt = select(Organization).where(Organization.id == org_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def create_org(
    name: str,
    slug: str,
    plan: OrganizationPlan,
    db: AsyncSession
) -> Organization:
    """
    Inserts a new organization record.
    """
    org = Organization(
        name=name,
        slug=slug.lower().strip(),
        plan=plan
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    
    # Seed default security policy for the new organization
    from app.services.security_svc import get_or_create_policy
    await get_or_create_policy(org.id, db)
    
    return org


async def update_org(
    org_id: uuid.UUID,
    name: Optional[str],
    plan: Optional[OrganizationPlan],
    db: AsyncSession
) -> Optional[Organization]:
    """
    Updates organization properties (name and plan).
    """
    org = await get_org_by_id(org_id, db)
    if not org:
        return None
        
    if name is not None:
        org.name = name
    if plan is not None:
        org.plan = plan
        
    await db.commit()
    await db.refresh(org)
    return org
