import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.models.base import Base, uuid_pk_column

class ABTest(Base):
    __tablename__ = "ab_tests"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_a: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_a: Mapped[str] = mapped_column(String(100), nullable=False)
    model_b: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_b: Mapped[str] = mapped_column(String(100), nullable=False)
    split_pct: Mapped[int] = mapped_column(Integer, default=20, server_default="20", nullable=False)
    test_mode: Mapped[str] = mapped_column(String(50), default="traffic_split", server_default="traffic_split", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", server_default="active", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    results: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")


class ABTestResult(Base):
    __tablename__ = "ab_test_results"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    test_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    variant: Mapped[str] = mapped_column(String(10), nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    latency: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    test: Mapped["ABTest"] = relationship("ABTest")
    organization: Mapped["Organization"] = relationship("Organization")


class ShadowResult(Base):
    __tablename__ = "shadow_results"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    test_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False, index=True)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_a_cost: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    model_b_cost: Mapped[float] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    model_a_latency: Mapped[int] = mapped_column(Integer, nullable=False)
    model_b_latency: Mapped[int] = mapped_column(Integer, nullable=False)
    model_a_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    model_b_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    test: Mapped["ABTest"] = relationship("ABTest")

