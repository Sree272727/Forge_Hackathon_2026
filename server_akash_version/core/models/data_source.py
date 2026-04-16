"""Data source model for uploaded spreadsheet metadata."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BIGINT, JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database.session import Base


def utc_now() -> datetime:
    """Return UTC timestamp without tzinfo for DB naive timestamp columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DataSource(Base):
    """Data source model for uploaded Excel files and extracted metadata."""

    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BIGINT, nullable=False)
    sheet_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    file_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    meta_info: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return string representation of DataSource."""
        return f"<DataSource(id={self.id}, user_id={self.user_id}, name={self.name})>"
