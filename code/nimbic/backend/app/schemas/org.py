from typing import Optional
import uuid
from pydantic import BaseModel, ConfigDict
from app.models.org import OrganizationPlan


class OrgCreate(BaseModel):
    """
    Schema for creating a new organization.
    """
    name: str
    slug: str
    plan: OrganizationPlan = OrganizationPlan.FREE


class OrgUpdate(BaseModel):
    """
    Schema for updating an organization's configuration.
    """
    name: Optional[str] = None
    plan: Optional[OrganizationPlan] = None


class OrgResponse(BaseModel):
    """
    Schema for organization responses.
    """
    id: uuid.UUID
    name: str
    slug: str
    plan: OrganizationPlan

    model_config = ConfigDict(from_attributes=True)
