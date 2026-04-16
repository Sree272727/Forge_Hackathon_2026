"""Conversation and LLM Usage tracking models."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.session import Base


def utc_now() -> datetime:
    """Return UTC timestamp without tzinfo for DB naive timestamp columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LLMCallType(str, Enum):
    """Types of LLM calls for tracking."""

    ASK_QUESTION = "ask_question"
    METADATA_EXTRACTION = "metadata_extraction"
    SEMANTIC_MAPPING = "semantic_mapping"
    CODE_GENERATION = "code_generation"
    ERROR_CORRECTION = "error_correction"


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    GEMINI = "gemini"


# Cost per 1M tokens (as of 2024) - update these as pricing changes
LLM_PRICING = {
    "openai": {
        "gpt-4o": {"input": Decimal("2.50"), "output": Decimal("10.00")},
        "gpt-4o-mini": {"input": Decimal("0.15"), "output": Decimal("0.60")},
        "gpt-4-turbo": {"input": Decimal("10.00"), "output": Decimal("30.00")},
    },
    "gemini": {
        "gemini-1.5-pro": {"input": Decimal("1.25"), "output": Decimal("5.00")},
        "gemini-1.5-flash": {"input": Decimal("0.075"), "output": Decimal("0.30")},
        "gemini-2.0-flash": {"input": Decimal("0.10"), "output": Decimal("0.40")},
    },
}


class Conversation(Base):
    """Model for tracking conversations with Excel data sources."""

    __tablename__ = "conversations"

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
    data_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Conversation metadata
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New Conversation")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Cost tracking for the entire conversation
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<Conversation(id={self.id}, title={self.title})>"

    def add_cost(self, input_tokens: int, output_tokens: int, cost_usd: Decimal) -> None:
        """Add tokens and cost to conversation totals."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += cost_usd
        self.last_message_at = utc_now()


class ConversationMessage(Base):
    """Model for individual messages in a conversation."""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Message content
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # For assistant messages - additional metadata
    code_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Token and cost tracking for this message
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))

    # Link to LLM usage records
    llm_usage_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("llm_usage.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages",
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<ConversationMessage(id={self.id}, role={self.role})>"


class LLMUsage(Base):
    """Model for tracking all LLM API calls and their costs.

    Uses a flexible `context` JSON column to store relevant IDs based on call_type:
    - ask_question: {"data_source_id": "...", "conversation_id": "...", "excel_schema_id": "..."}
    - metadata_extraction: {"data_source_id": "..."}
    - semantic_mapping: {"data_source_id": "...", "excel_schema_id": "..."}
    - code_generation: {"data_source_id": "...", "conversation_id": "..."}
    """

    __tablename__ = "llm_usage"

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

    # Call type
    call_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # LLMCallType values

    # Flexible context - stores relevant IDs based on call_type
    # Example: {"data_source_id": "uuid", "conversation_id": "uuid", "excel_schema_id": "uuid"}
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # LLM details
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # 'openai' or 'gemini'
    model: Mapped[str] = mapped_column(String(50), nullable=False)

    # Token usage
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cost calculation (in USD)
    input_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    output_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))

    # Request/Response metadata (optional additional info)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    # Performance
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<LLMUsage(id={self.id}, type={self.call_type}, cost=${self.total_cost_usd})>"

    # Helper methods to extract IDs from context
    @property
    def data_source_id(self) -> str | None:
        """Get data_source_id from context."""
        return self.context.get("data_source_id")

    @property
    def conversation_id(self) -> str | None:
        """Get conversation_id from context."""
        return self.context.get("conversation_id")

    @property
    def excel_schema_id(self) -> str | None:
        """Get excel_schema_id from context."""
        return self.context.get("excel_schema_id")

    @property
    def message_id(self) -> str | None:
        """Get message_id from context."""
        return self.context.get("message_id")

    @classmethod
    def calculate_cost(
        cls,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Calculate cost based on provider, model, and token counts.

        Returns:
            Tuple of (input_cost, output_cost, total_cost) in USD.
        """
        provider_pricing = LLM_PRICING.get(provider.lower(), {})
        model_pricing = provider_pricing.get(model.lower(), {"input": Decimal("0"), "output": Decimal("0")})

        # Cost per 1M tokens
        input_cost = (Decimal(input_tokens) / Decimal("1000000")) * model_pricing["input"]
        output_cost = (Decimal(output_tokens) / Decimal("1000000")) * model_pricing["output"]
        total_cost = input_cost + output_cost

        return input_cost, output_cost, total_cost


class FileUploadUsage(Base):
    """Model for tracking file upload costs (if applicable)."""

    __tablename__ = "file_upload_usage"

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
    data_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # File details
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Processing costs (LLM calls for metadata extraction)
    metadata_extraction_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    semantic_mapping_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    total_processing_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))

    # Token usage for processing
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<FileUploadUsage(id={self.id}, data_source_id={self.data_source_id})>"
