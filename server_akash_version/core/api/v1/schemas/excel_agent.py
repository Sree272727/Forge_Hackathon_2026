"""Pydantic schemas for Excel agent API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProcessDataSourceRequest(BaseModel):
    """Request to process a data source."""

    force_reprocess: bool = Field(
        default=False,
        description="Force reprocessing even if already processed",
    )


class ProcessDataSourceResponse(BaseModel):
    """Response from processing a data source."""

    schema_id: str
    data_source_id: str
    processing_status: str
    is_ready_for_queries: bool
    workbook_title: str | None = None
    workbook_purpose: str | None = None
    domain: str | None = None
    context_header_for_qa: str | None = None
    total_sections: int = 0
    total_merged_regions: int = 0
    detected_colors: list[str] = []
    queryable_questions: list[str] = []
    data_quality_notes: list[str] = []
    processing_error: str | None = None
    processed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ExcelSchemaResponse(BaseModel):
    """Full Excel schema response."""

    id: str
    data_source_id: str
    processing_status: str
    is_ready_for_queries: bool
    workbook_title: str | None = None
    workbook_purpose: str | None = None
    domain: str | None = None
    context_header_for_qa: str | None = None
    manifest: dict = {}
    semantic_schema: dict = {}
    enrichment: dict = {}
    query_routing: dict = {}
    detected_colors: list[str] = []
    total_sections: int = 0
    total_merged_regions: int = 0
    queryable_questions: list[str] = []
    data_quality_notes: list[str] = []
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None

    model_config = {"from_attributes": True}


class AskQuestionRequest(BaseModel):
    """Request to ask a question about Excel data."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Natural language question about the data",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation ID to continue existing conversation",
    )


class AskQuestionResponse(BaseModel):
    """Response from asking a question."""

    success: bool
    answer: Any = None
    code_used: str | None = None
    iterations: int | None = None
    error: str | None = None
    execution_time_ms: int
    query_id: str
    conversation_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class QueryHistoryItem(BaseModel):
    """Single query history item."""

    id: str
    question: str
    answer: Any = None
    code_used: str | None = None
    success: bool
    error_message: str | None = None
    execution_time_ms: int | None = None
    iterations_used: int = 1
    created_at: datetime

    model_config = {"from_attributes": True}


class QueryHistoryResponse(BaseModel):
    """Response containing query history."""

    items: list[QueryHistoryItem]
    total: int


class SuggestedQuestionsResponse(BaseModel):
    """Response containing suggested questions."""

    questions: list[str]
    data_source_id: str


class ManifestSummaryResponse(BaseModel):
    """Summary of the extracted manifest."""

    sheet_count: int
    sheet_names: list[str]
    total_merged_regions: int
    total_sections: int
    detected_colors: list[str]
    sheets: dict[str, dict]


class SheetEnrichmentResponse(BaseModel):
    """Enrichment data for a single sheet."""

    sheet_name: str
    semantic_title: str = ""
    domain: str = "general"
    primary_purpose: str = ""
    time_dimension: dict = {}
    key_metrics: list[dict] = []
    dimensions: list[dict] = []
    detected_tables: list[dict] = []
    section_labels: list[dict] = []
    answerable_question_types: list[str] = []
    data_quality_flags: list[dict] = []
    retrieval_hints: dict = {}
    confidence: str = "medium"


class EnrichmentResponse(BaseModel):
    """Full enrichment response for a workbook."""

    workbook_title: str = ""
    workbook_purpose: str = ""
    domain: str = "general"
    context_header_for_qa: str = ""
    sheet_index: list[dict] = []
    cross_sheet_relationships: list[dict] = []
    global_metrics: list[str] = []
    query_routing: dict = {}
    sheets: dict[str, SheetEnrichmentResponse] = {}

    model_config = {"from_attributes": True}


class SchemaInfoResponse(BaseModel):
    """Quick info about the schema."""

    data_source_id: str
    processing_status: str
    is_ready_for_queries: bool
    workbook_title: str | None = None
    workbook_purpose: str | None = None
    domain: str | None = None
    context_header_for_qa: str | None = None
    sheet_count: int = 0
    queryable_questions_count: int = 0
    has_data_quality_notes: bool = False
    has_enrichment: bool = False


# ==================== Conversation Schemas ====================


class ConversationMessageResponse(BaseModel):
    """Single message in a conversation."""

    id: str
    role: str
    content: str
    code_used: str | None = None
    execution_time_ms: int | None = None
    is_error: bool = False
    error_message: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    """Response for a single conversation."""

    id: str
    data_source_id: str
    title: str
    is_active: bool = True
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    messages: list[ConversationMessageResponse] = []

    model_config = {"from_attributes": True}


class ConversationListItem(BaseModel):
    """Summary item for conversation list."""

    id: str
    data_source_id: str
    title: str
    total_cost_usd: float = 0
    message_count: int = 0
    created_at: datetime
    last_message_at: datetime | None = None

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    """Response containing list of conversations."""

    items: list[ConversationListItem]
    total: int


class UsageSummaryResponse(BaseModel):
    """Response containing usage statistics."""

    period_days: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    total_calls: int
    by_call_type: dict[str, dict]


class LLMUsageResponse(BaseModel):
    """Response for a single LLM usage record."""

    id: str
    call_type: str
    context: dict = {}  # Flexible context with relevant IDs
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    total_cost_usd: float
    latency_ms: int | None = None
    success: bool
    created_at: datetime

    model_config = {"from_attributes": True}
