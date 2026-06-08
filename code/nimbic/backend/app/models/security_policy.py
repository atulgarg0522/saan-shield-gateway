import enum
import uuid
from sqlalchemy import Enum, ForeignKey, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, uuid_pk_column

class PiiActionEnum(str, enum.Enum):
    allow = "allow"
    redact = "redact"
    warn = "warn"
    block = "block"

    ALLOW = "allow"
    REDACT = "redact"
    WARN = "warn"
    BLOCK = "block"


class PolicyActionEnum(str, enum.Enum):
    allow = "allow"
    warn = "warn"
    block = "block"

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class SecurityPolicy(Base, TimestampMixin):
    __tablename__ = "security_policies"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )
    pii_action: Mapped[PiiActionEnum] = mapped_column(
        Enum(PiiActionEnum, name="pii_action_enum", native_enum=True),
        default=PiiActionEnum.redact,
        server_default="redact",
        nullable=False
    )
    code_action: Mapped[PolicyActionEnum] = mapped_column(
        Enum(PolicyActionEnum, name="policy_action_enum", native_enum=True),
        default=PolicyActionEnum.warn,
        server_default="warn",
        nullable=False
    )
    sensitive_action: Mapped[PolicyActionEnum] = mapped_column(
        Enum(PolicyActionEnum, name="policy_action_enum", native_enum=True),
        default=PolicyActionEnum.warn,
        server_default="warn",
        nullable=False
    )
    blocked_regions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    allowed_providers_by_region: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    custom_patterns: Mapped[list] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Relationship
    organization: Mapped["Organization"] = relationship("Organization")

    def __repr__(self) -> str:
        return f"<SecurityPolicy(id={self.id}, org_id={self.org_id}, is_active={self.is_active})>"
