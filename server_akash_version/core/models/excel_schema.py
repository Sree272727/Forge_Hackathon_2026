"""Excel schema model for storing processed workbook metadata."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.session import Base


def utc_now() -> datetime:
    """Return UTC timestamp without tzinfo for DB naive timestamp columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProcessingStatus(str):
    """Processing status enum values."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    MAPPING = "mapping"
    ENRICHING = "enriching"  # New: semantic enrichment phase
    COMPLETED = "completed"
    FAILED = "failed"


class ExcelSchema(Base):
    """Model for storing processed Excel workbook schemas and metadata.

    This stores the output of the multi-agent processing pipeline:
    - Visual metadata from the extractor
    - Semantic schema from the mapper
    - Processing status and errors
    """

    __tablename__ = "excel_schemas"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    data_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Processing status
    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Visual metadata from extractor (Phase A)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Semantic schema from mapper (Phase B) - legacy, kept for compatibility
    semantic_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Semantic enrichment from SemanticEnricher (Phase C - new enhanced enrichment)
    enrichment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Quick access fields from enrichment
    workbook_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    workbook_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True, default="general")

    # Context header for Q&A - injected at the top of every query prompt
    context_header_for_qa: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Query routing recommendations
    query_routing: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    detected_colors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    total_sections: Mapped[int] = mapped_column(default=0)
    total_merged_regions: Mapped[int] = mapped_column(default=0)

    # Queryable questions (from semantic mapper)
    queryable_questions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Data quality notes
    data_quality_notes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Ready flag
    is_ready_for_queries: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        """Return string representation of ExcelSchema."""
        return f"<ExcelSchema(id={self.id}, data_source_id={self.data_source_id}, status={self.processing_status})>"

    def mark_extracting(self) -> None:
        """Mark as currently extracting metadata."""
        self.processing_status = ProcessingStatus.EXTRACTING
        self.processing_error = None

    def mark_mapping(self) -> None:
        """Mark as currently creating semantic mapping."""
        self.processing_status = ProcessingStatus.MAPPING

    def mark_enriching(self) -> None:
        """Mark as currently performing semantic enrichment."""
        self.processing_status = ProcessingStatus.ENRICHING

    def mark_completed(
        self,
        manifest: dict,
        semantic_schema: dict,
        workbook_purpose: str | None = None,
        detected_colors: list[str] | None = None,
        total_sections: int = 0,
        total_merged_regions: int = 0,
        queryable_questions: list[str] | None = None,
        data_quality_notes: list[str] | None = None,
        enrichment: dict | None = None,
        workbook_title: str | None = None,
        domain: str | None = None,
        context_header_for_qa: str | None = None,
        query_routing: dict | None = None,
    ) -> None:
        """Mark processing as completed with results."""
        self.processing_status = ProcessingStatus.COMPLETED
        self.manifest = manifest
        self.semantic_schema = semantic_schema
        self.workbook_purpose = workbook_purpose
        self.detected_colors = detected_colors or []
        self.total_sections = total_sections
        self.total_merged_regions = total_merged_regions
        self.queryable_questions = queryable_questions or []
        self.data_quality_notes = data_quality_notes or []

        # New enrichment fields
        self.enrichment = enrichment or {}
        self.workbook_title = workbook_title
        self.domain = domain or "general"
        self.context_header_for_qa = context_header_for_qa
        self.query_routing = query_routing or {}

        self.is_ready_for_queries = True
        self.processed_at = utc_now()
        self.processing_error = None

    def mark_failed(self, error: str) -> None:
        """Mark processing as failed with error."""
        self.processing_status = ProcessingStatus.FAILED
        self.processing_error = error
        self.is_ready_for_queries = False


class QueryHistory(Base):
    """Model for storing query history against Excel files."""

    __tablename__ = "query_history"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    excel_schema_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("excel_schemas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Query details
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    code_used: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Execution status
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(nullable=True)
    iterations_used: Mapped[int] = mapped_column(default=1)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    def __repr__(self) -> str:
        """Return string representation of QueryHistory."""
        return f"<QueryHistory(id={self.id}, question={self.question[:50]}..., success={self.success})>"
