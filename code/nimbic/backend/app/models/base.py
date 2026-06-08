import uuid
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID


class Base(DeclarativeBase):
    """
    SQLAlchemy ORM base metadata registry class.
    """
    pass


class TimestampMixin:
    """
    Standard mixin to add auto-managed created_at and updated_at timestamps.
    Defaults to UTC timezone.
    """
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=func.now(),
        nullable=False
    )


def uuid_pk_column() -> Mapped[uuid.UUID]:
    """
    Helper function generating a UUID primary key column.
    Generates client-side via uuid.uuid4 and server-side via PostgreSQL gen_random_uuid().
    """
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        nullable=False
    )
