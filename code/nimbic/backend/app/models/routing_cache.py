import enum
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy import String, Integer, Boolean, Text, JSON, Numeric, DateTime, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from app.models.base import Base, uuid_pk_column


class CostSavingsSource(str, enum.Enum):
    cache_hit = "cache_hit"
    model_routing = "model_routing"
    faq_hit = "faq_hit"


class PromptEmbedding(Base):
    __tablename__ = "prompt_embeddings"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    embedding: Mapped[Vector] = mapped_column(Vector(384), nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")


class OrgFAQCache(Base):
    __tablename__ = "org_faq_cache"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Vector] = mapped_column(Vector(384), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=func.now(),
        nullable=False
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    conditions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    target_model: Mapped[str] = mapped_column(String(100), nullable=False)
    target_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")


class CostSavingsLog(Base):
    __tablename__ = "cost_savings_log"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    actual_model: Mapped[str] = mapped_column(String(100), nullable=False)
    actual_cost_usd: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    baseline_model: Mapped[str] = mapped_column(String(100), default="gpt-4o", server_default="gpt-4o", nullable=False)
    baseline_cost_usd: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    savings_usd: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    source: Mapped[CostSavingsSource] = mapped_column(
        Enum(CostSavingsSource, name="cost_savings_source_enum", native_enum=True, create_type=False),
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
