"""User model for authentication and user management."""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database.session import Base


class AuthProvider(str, Enum):
    """Authentication provider types."""

    LOCAL = "local"
    GOOGLE = "google"


class User(Base):
    """User model for storing user information."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Profile information
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_picture: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Authentication details
    auth_provider: Mapped[str] = mapped_column(String(50), default=AuthProvider.LOCAL.value, nullable=False)
    google_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)

    # Account status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        """Return string representation of User."""
        return f"<User(id={self.id}, email={self.email}, provider={self.auth_provider})>"
