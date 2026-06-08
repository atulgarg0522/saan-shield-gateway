from typing import Optional
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.models.request_log import ProviderEnum


class ProviderConfigRequest(BaseModel):
    """
    Schema for adding or updating an upstream provider credential mapping.
    """
    provider: ProviderEnum
    api_key: str
    base_url: Optional[str] = None


class ProviderConfigResponse(BaseModel):
    """
    Schema for provider response logs. Never exposes encrypted secret key hashes.
    """
    id: uuid.UUID
    provider: ProviderEnum
    base_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
