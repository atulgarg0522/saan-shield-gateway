import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import ForeignKey, String, JSON, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, uuid_pk_column


class ApiKey(Base, TimestampMixin):
    """
    Gateway authorization API Key associated with a specific organization.
    Stores the secure bcrypt hash representation of the key.
    """
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Store bcrypt hash securely, indexed for fast verification lookups
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    
    # Prefix (e.g. nim_abc1) shown in management UI
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    
    # Array of authorized capabilities (e.g. ["proxy", "logs:read"])
    scopes: Mapped[list] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="api_keys")
    request_logs: Mapped[List["RequestLog"]] = relationship(
        "RequestLog",
        back_populates="api_key",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ApiKey(id={self.id}, name='{self.name}', prefix='{self.key_prefix}', is_active={self.is_active})>"
