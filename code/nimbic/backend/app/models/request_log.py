import enum
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import ForeignKey, String, Integer, Numeric, JSON, Enum, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, uuid_pk_column


class ProviderEnum(str, enum.Enum):
    """
    Supported upstream AI foundational model providers.
    """
    openai = "openai"
    anthropic = "anthropic"
    gemini = "gemini"
    azure_openai = "azure_openai"
    bedrock = "bedrock"

    # Keep uppercase properties for backward compatibility
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    AZURE_OPENAI = "azure_openai"
    BEDROCK = "bedrock"


class RequestLog(Base):
    """
    Immutable analytic log record for each outbound prompt proxy call.
    Maintains strict token balances, latency tracking, and exact provider billing metrics.
    """
    __tablename__ = "requests_log"

    id: Mapped[uuid.UUID] = uuid_pk_column()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Keep request log records for audits even if the API Key is revoked/deleted
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Globally unique request tracking id (e.g. req_abc123) for tracing
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    provider: Mapped[ProviderEnum] = mapped_column(
        Enum(ProviderEnum, name="provider_enum", native_enum=True),
        nullable=False,
        index=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    
    # Store exact costs using Numeric to prevent floating point cents rounding errors
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Optional end-user tracing tag
    user_identifier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    
    # Client-supplied arbitrary properties (e.g. session-id, pipeline-version)
    request_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # FinOps attribution columns
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Logs are write-once/immutable, hence only created_at is mapped
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="request_logs")
    api_key: Mapped[Optional["ApiKey"]] = relationship("ApiKey", back_populates="request_logs")
    team: Mapped[Optional["Team"]] = relationship("Team", back_populates="request_logs")
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="request_logs")

    def __repr__(self) -> str:
        return f"<RequestLog(id={self.id}, request_id='{self.request_id}', provider='{self.provider}', model='{self.model}', status={self.status_code})>"
