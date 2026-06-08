import enum
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import ForeignKey, String, Integer, Numeric, Boolean, DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, TimestampMixin, uuid_pk_column


class BudgetScopeEnum(str, enum.Enum):
    org = "org"
    team = "team"
    project = "project"
    user = "user"


class BudgetPeriodEnum(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class BudgetAlertTypeEnum(str, enum.Enum):
    soft_warning = "soft_warning"
    hard_block = "hard_block"


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    budget_limit_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    budget_alert_pct: Mapped[int] = mapped_column(Integer, default=80, server_default="80", nullable=False)

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    projects: Mapped[List["Project"]] = relationship("Project", back_populates="team", cascade="all, delete-orphan")
    request_logs: Mapped[List["RequestLog"]] = relationship("RequestLog", back_populates="team")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    budget_limit_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    budget_alert_pct: Mapped[int] = mapped_column(Integer, default=80, server_default="80", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    team: Mapped[Optional[Team]] = relationship("Team", back_populates="projects")
    request_logs: Mapped[List["RequestLog"]] = relationship("RequestLog", back_populates="project")


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    scope_type: Mapped[BudgetScopeEnum] = mapped_column(
        Enum(BudgetScopeEnum, name="budget_scope_enum", native_enum=True),
        nullable=False,
        index=True
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    period: Mapped[BudgetPeriodEnum] = mapped_column(
        Enum(BudgetPeriodEnum, name="budget_period_enum", native_enum=True),
        nullable=False,
        index=True
    )
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    alert_pct: Mapped[int] = mapped_column(Integer, default=80, server_default="80", nullable=False)
    hard_limit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    alerts: Mapped[List["BudgetAlert"]] = relationship("BudgetAlert", back_populates="budget", cascade="all, delete-orphan")


class BudgetAlert(Base):
    __tablename__ = "budget_alerts"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    alert_type: Mapped[BudgetAlertTypeEnum] = mapped_column(
        Enum(BudgetAlertTypeEnum, name="budget_alert_type_enum", native_enum=True),
        nullable=False,
        index=True
    )
    usage_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    usage_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    budget: Mapped[Budget] = relationship("Budget", back_populates="alerts")
    organization: Mapped["Organization"] = relationship("Organization")
