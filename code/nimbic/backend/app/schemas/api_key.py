from typing import List, Optional
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class KeyCreateRequest(BaseModel):
    """
    Schema for creating a new gateway API key.
    """
    name: str
    scopes: List[str] = ["proxy"]
    expires_at: Optional[datetime] = None


class KeyUpdateRequest(BaseModel):
    """
    Schema for modifying an API key's details.
    """
    name: Optional[str] = None
    scopes: Optional[List[str]] = None
    is_active: Optional[bool] = None


class KeyCreateResponse(BaseModel):
    """
    Response schema returning the raw key once upon generation.
    """
    id: uuid.UUID
    name: str
    key_prefix: str
    raw_key: str
    scopes: List[str]
    is_active: bool
    expires_at: Optional[datetime] = None


class KeyDetailsResponse(BaseModel):
    """
    Standard API Key details payload schema (excluding the secret hash).
    """
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: List[str]
    is_active: bool
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
