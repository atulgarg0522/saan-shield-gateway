import enum
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, uuid_pk_column

class ViolationTypeEnum(str, enum.Enum):
    pii = "pii"
    source_code = "source_code"
    sensitive_content = "sensitive_content"
    data_residency = "data_residency"

    PII = "pii"
    SOURCE_CODE = "source_code"
    SENSITIVE_CONTENT = "sensitive_content"
    DATA_RESIDENCY = "data_residency"


class SeverityEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ViolationActionEnum(str, enum.Enum):
    allowed = "allowed"
    redacted = "redacted"
    warned = "warned"
    blocked = "blocked"

    ALLOWED = "allowed"
    REDACTED = "redacted"
    WARNED = "warned"
    BLOCKED = "blocked"


class SecurityViolation(Base):
    __tablename__ = "security_violations"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    violation_type: Mapped[ViolationTypeEnum] = mapped_column(
        Enum(ViolationTypeEnum, name="violation_type_enum", native_enum=True),
        nullable=False,
        index=True
    )
    severity: Mapped[SeverityEnum] = mapped_column(
        Enum(SeverityEnum, name="severity_enum", native_enum=True),
        nullable=False,
        index=True
    )
    action_taken: Mapped[ViolationActionEnum] = mapped_column(
        Enum(ViolationActionEnum, name="action_taken_enum", native_enum=True),
        nullable=False,
        index=True
    )
    details: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    prompt_snippet: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False
    )

    # Relationship
    organization: Mapped["Organization"] = relationship("Organization")

    def __repr__(self) -> str:
        return f"<SecurityViolation(id={self.id}, org_id={self.org_id}, violation_type='{self.violation_type}', action_taken='{self.action_taken}')>"
