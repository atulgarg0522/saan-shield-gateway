import enum
import uuid
from typing import List
from sqlalchemy import String, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, uuid_pk_column


class OrganizationPlan(str, enum.Enum):
    """
    Supported billing and rate-limiting tiers for saan-ai-gateway organizations.
    """
    free = "free"
    starter = "starter"
    pro = "pro"
    enterprise = "enterprise"

    # Upper case for backward compatibility
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Organization(Base, TimestampMixin):
    """
    Organization holding and billing entity. Owns API keys, outbound configurations, and request trace telemetry logs.
    """
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    plan: Mapped[OrganizationPlan] = mapped_column(
        Enum(OrganizationPlan, name="organization_plan_enum", native_enum=True),
        default=OrganizationPlan.FREE,
        server_default=OrganizationPlan.FREE.value,
        nullable=False
    )

    # Bidirectional ORM relationships (cascade delete-orphan ensures cleanup)
    api_keys: Mapped[List["ApiKey"]] = relationship(
        "ApiKey",
        back_populates="organization",
        cascade="all, delete-orphan"
    )
    provider_configs: Mapped[List["ProviderConfig"]] = relationship(
        "ProviderConfig",
        back_populates="organization",
        cascade="all, delete-orphan"
    )
    request_logs: Mapped[List["RequestLog"]] = relationship(
        "RequestLog",
        back_populates="organization",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name='{self.name}', slug='{self.slug}', plan='{self.plan}')>"
