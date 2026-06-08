import uuid
from typing import Optional
from sqlalchemy import ForeignKey, String, Boolean, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, uuid_pk_column
from app.models.request_log import ProviderEnum


class ProviderConfig(Base, TimestampMixin):
    """
    Credentials and endpoint routing properties for outbound provider integrations (e.g. custom Azure endpoints).
    """
    __tablename__ = "provider_configs"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Reuse ProviderEnum definition mapping PG native provider enums
    provider: Mapped[ProviderEnum] = mapped_column(
        Enum(ProviderEnum, name="provider_enum", native_enum=True),
        nullable=False,
        index=True
    )
    
    # Ciphertext of raw provider credential keys (e.g., AES-256 encrypted string)
    api_key_encrypted: Mapped[str] = mapped_column(String(512), nullable=False)
    
    # Custom API endpoints (nullable, e.g. for Azure or self-hosted API proxies)
    base_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="provider_configs")

    def __repr__(self) -> str:
        return f"<ProviderConfig(id={self.id}, provider='{self.provider}', is_active={self.is_active})>"
