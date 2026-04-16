"""API routes for Excel agent operations."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.schemas.excel_agent import (
    AskQuestionRequest,
    AskQuestionResponse,
    ConversationListItem,
    ConversationListResponse,
    ConversationResponse,
    EnrichmentResponse,
    ExcelSchemaResponse,
    LLMUsageResponse,
    ManifestSummaryResponse,
    ProcessDataSourceRequest,
    ProcessDataSourceResponse,
    QueryHistoryItem,
    QueryHistoryResponse,
    SchemaInfoResponse,
    SheetEnrichmentResponse,
    SuggestedQuestionsResponse,
    UsageSummaryResponse,
)
from core.database.session import get_db_session
from core.dependencies.auth import get_current_user
from core.exceptions.base import BusinessLogicError, NotFoundError, ValidationError
from core.logging import get_logger
from core.models.user import User
from core.services.excel_agent import ExcelAgentService
from core.services.conversation import ConversationService

logger = get_logger(__name__)

router = APIRouter(prefix="/excel-agent", tags=["Excel Agent"])


async def get_excel_agent_service(
    session: AsyncSession = Depends(get_db_session),
) -> ExcelAgentService:
    """Dependency to get Excel agent service."""
    return ExcelAgentService(session)


async def get_conversation_service(
    session: AsyncSession = Depends(get_db_session),
) -> ConversationService:
    """Dependency to get Conversation service."""
    return ConversationService(session)


@router.post(
    "/data-sources/{data_source_id}/process",
    response_model=ProcessDataSourceResponse,
    status_code=status.HTTP_200_OK,
    summary="Process a data source",
    description="Process an Excel file through the agent pipeline to extract metadata and create semantic schema.",
)
async def process_data_source(
    data_source_id: str,
    request: ProcessDataSourceRequest = ProcessDataSourceRequest(),
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> ProcessDataSourceResponse:
    """Process a data source through the Excel agent pipeline."""
    try:
        schema = await service.process_data_source(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
            force_reprocess=request.force_reprocess,
        )

        return ProcessDataSourceResponse(
            schema_id=schema.id,
            data_source_id=schema.data_source_id,
            processing_status=schema.processing_status,
            is_ready_for_queries=schema.is_ready_for_queries,
            workbook_title=schema.workbook_title,
            workbook_purpose=schema.workbook_purpose,
            domain=schema.domain,
            context_header_for_qa=schema.context_header_for_qa,
            total_sections=schema.total_sections,
            total_merged_regions=schema.total_merged_regions,
            detected_colors=schema.detected_colors,
            queryable_questions=schema.queryable_questions,
            data_quality_notes=schema.data_quality_notes,
            processing_error=schema.processing_error,
            processed_at=schema.processed_at,
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except BusinessLogicError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get(
    "/data-sources/{data_source_id}/schema",
    response_model=ExcelSchemaResponse,
    summary="Get Excel schema",
    description="Get the full Excel schema for a processed data source.",
)
async def get_schema(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> ExcelSchemaResponse:
    """Get the Excel schema for a data source."""
    try:
        schema = await service.get_schema(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
        )

        return ExcelSchemaResponse(
            id=schema.id,
            data_source_id=schema.data_source_id,
            processing_status=schema.processing_status,
            is_ready_for_queries=schema.is_ready_for_queries,
            workbook_title=schema.workbook_title,
            workbook_purpose=schema.workbook_purpose,
            domain=schema.domain,
            context_header_for_qa=schema.context_header_for_qa,
            manifest=schema.manifest,
            semantic_schema=schema.semantic_schema,
            enrichment=schema.enrichment,
            query_routing=schema.query_routing,
            detected_colors=schema.detected_colors,
            total_sections=schema.total_sections,
            total_merged_regions=schema.total_merged_regions,
            queryable_questions=schema.queryable_questions,
            data_quality_notes=schema.data_quality_notes,
            processing_error=schema.processing_error,
            created_at=schema.created_at,
            updated_at=schema.updated_at,
            processed_at=schema.processed_at,
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/data-sources/{data_source_id}/schema/info",
    response_model=SchemaInfoResponse,
    summary="Get schema info",
    description="Get quick info about the schema status.",
)
async def get_schema_info(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> SchemaInfoResponse:
    """Get quick info about the schema status."""
    try:
        schema = await service.get_schema(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
        )

        sheet_count = 0
        if schema.manifest and "sheet_count" in schema.manifest:
            sheet_count = schema.manifest["sheet_count"]

        return SchemaInfoResponse(
            data_source_id=schema.data_source_id,
            processing_status=schema.processing_status,
            is_ready_for_queries=schema.is_ready_for_queries,
            workbook_title=schema.workbook_title,
            workbook_purpose=schema.workbook_purpose,
            domain=schema.domain,
            context_header_for_qa=schema.context_header_for_qa,
            sheet_count=sheet_count,
            queryable_questions_count=len(schema.queryable_questions or []),
            has_data_quality_notes=bool(schema.data_quality_notes),
            has_enrichment=bool(schema.enrichment),
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/data-sources/{data_source_id}/manifest",
    response_model=ManifestSummaryResponse,
    summary="Get manifest summary",
    description="Get the visual metadata manifest summary.",
)
async def get_manifest_summary(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> ManifestSummaryResponse:
    """Get the manifest summary for a data source."""
    try:
        schema = await service.get_schema(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
        )

        manifest = schema.manifest or {}

        return ManifestSummaryResponse(
            sheet_count=manifest.get("sheet_count", 0),
            sheet_names=manifest.get("sheet_names", []),
            total_merged_regions=manifest.get("total_merged_regions", 0),
            total_sections=manifest.get("total_sections", 0),
            detected_colors=manifest.get("detected_colors", []),
            sheets=manifest.get("sheets", {}),
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/data-sources/{data_source_id}/enrichment",
    response_model=EnrichmentResponse,
    summary="Get semantic enrichment",
    description="Get the semantic enrichment data including domain, metrics, dimensions, and context header for Q&A.",
)
async def get_enrichment(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> EnrichmentResponse:
    """Get the semantic enrichment for a data source."""
    try:
        schema = await service.get_schema(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
        )

        enrichment = schema.enrichment or {}

        # Build sheet enrichments
        sheets = {}
        for sheet_name, sheet_data in enrichment.get("sheets", {}).items():
            sheets[sheet_name] = SheetEnrichmentResponse(
                sheet_name=sheet_data.get("sheet_name", sheet_name),
                semantic_title=sheet_data.get("semantic_title", ""),
                domain=sheet_data.get("domain", "general"),
                primary_purpose=sheet_data.get("primary_purpose", ""),
                time_dimension=sheet_data.get("time_dimension", {}),
                key_metrics=sheet_data.get("key_metrics", []),
                dimensions=sheet_data.get("dimensions", []),
                detected_tables=sheet_data.get("detected_tables", []),
                section_labels=sheet_data.get("section_labels", []),
                answerable_question_types=sheet_data.get("answerable_question_types", []),
                data_quality_flags=sheet_data.get("data_quality_flags", []),
                retrieval_hints=sheet_data.get("retrieval_hints", {}),
                confidence=sheet_data.get("confidence", "medium"),
            )

        return EnrichmentResponse(
            workbook_title=enrichment.get("workbook_title", ""),
            workbook_purpose=enrichment.get("workbook_purpose", ""),
            domain=enrichment.get("domain", "general"),
            context_header_for_qa=enrichment.get("context_header_for_qa", ""),
            sheet_index=enrichment.get("sheet_index", []),
            cross_sheet_relationships=enrichment.get("cross_sheet_relationships", []),
            global_metrics=enrichment.get("global_metrics", []),
            query_routing=enrichment.get("recommended_query_routing", {}),
            sheets=sheets,
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.post(
    "/data-sources/{data_source_id}/ask",
    response_model=AskQuestionResponse,
    summary="Ask a question",
    description="Ask a natural language question about the Excel data.",
)
async def ask_question(
    data_source_id: str,
    request: AskQuestionRequest,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> AskQuestionResponse:
    """Ask a question about the Excel data."""
    try:
        result = await service.ask_question(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
            question=request.question,
            conversation_id=request.conversation_id,
        )

        return AskQuestionResponse(
            success=result["success"],
            answer=result.get("answer"),
            code_used=result.get("code_used"),
            iterations=result.get("iterations"),
            error=result.get("error"),
            execution_time_ms=result["execution_time_ms"],
            query_id=result["query_id"],
            conversation_id=result.get("conversation_id"),
            input_tokens=result.get("input_tokens"),
            output_tokens=result.get("output_tokens"),
            cost_usd=result.get("cost_usd"),
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except BusinessLogicError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get(
    "/data-sources/{data_source_id}/questions/suggested",
    response_model=SuggestedQuestionsResponse,
    summary="Get suggested questions",
    description="Get AI-generated suggested questions for the data.",
)
async def get_suggested_questions(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> SuggestedQuestionsResponse:
    """Get suggested questions for the data."""
    try:
        questions = await service.get_suggested_questions(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
        )

        return SuggestedQuestionsResponse(
            questions=questions,
            data_source_id=data_source_id,
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/data-sources/{data_source_id}/queries/history",
    response_model=QueryHistoryResponse,
    summary="Get query history",
    description="Get the history of questions asked about this data source.",
)
async def get_query_history(
    data_source_id: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    service: ExcelAgentService = Depends(get_excel_agent_service),
) -> QueryHistoryResponse:
    """Get query history for a data source."""
    try:
        history = await service.get_query_history(
            data_source_id=data_source_id,
            user_id=str(current_user.id),
            limit=limit,
        )

        items = [
            QueryHistoryItem(
                id=item.id,
                question=item.question,
                answer=item.answer,
                code_used=item.code_used,
                success=item.success,
                error_message=item.error_message,
                execution_time_ms=item.execution_time_ms,
                iterations_used=item.iterations_used,
                created_at=item.created_at,
            )
            for item in history
        ]

        return QueryHistoryResponse(
            items=items,
            total=len(items),
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# ==================== Conversation Endpoints ====================


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List conversations",
    description="Get a list of all conversations for the current user.",
)
async def list_conversations(
    data_source_id: str | None = Query(None, description="Filter by data source"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=100, description="Maximum records to return"),
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationListResponse:
    """List conversations for the current user."""
    conversation_data, total = await service.list_conversations(
        user_id=str(current_user.id),
        data_source_id=data_source_id,
        skip=skip,
        limit=limit,
    )

    items = [
        ConversationListItem(
            id=item["conversation"].id,
            data_source_id=item["conversation"].data_source_id,
            title=item["conversation"].title,
            total_cost_usd=float(item["conversation"].total_cost_usd),
            message_count=item["message_count"],
            created_at=item["conversation"].created_at,
            last_message_at=item["conversation"].last_message_at,
        )
        for item in conversation_data
    ]

    return ConversationListResponse(items=items, total=total)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get conversation",
    description="Get a specific conversation with all its messages.",
)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    """Get a conversation with its messages."""
    try:
        conversation = await service.get_conversation(
            conversation_id=conversation_id,
            user_id=str(current_user.id),
            include_messages=True,
        )

        return ConversationResponse(
            id=conversation.id,
            data_source_id=conversation.data_source_id,
            title=conversation.title,
            is_active=conversation.is_active,
            total_input_tokens=conversation.total_input_tokens,
            total_output_tokens=conversation.total_output_tokens,
            total_cost_usd=float(conversation.total_cost_usd),
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            last_message_at=conversation.last_message_at,
            messages=[
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "code_used": msg.code_used,
                    "execution_time_ms": msg.execution_time_ms,
                    "is_error": msg.is_error,
                    "error_message": msg.error_message,
                    "input_tokens": msg.input_tokens,
                    "output_tokens": msg.output_tokens,
                    "cost_usd": float(msg.cost_usd),
                    "created_at": msg.created_at,
                }
                for msg in (conversation.messages or [])
            ],
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete conversation",
    description="Delete a conversation and all its messages.",
)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> None:
    """Delete a conversation."""
    try:
        await service.delete_conversation(
            conversation_id=conversation_id,
            user_id=str(current_user.id),
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.patch(
    "/conversations/{conversation_id}/title",
    response_model=ConversationResponse,
    summary="Update conversation title",
    description="Update the title of a conversation.",
)
async def update_conversation_title(
    conversation_id: str,
    title: str = Query(..., min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    """Update conversation title."""
    try:
        conversation = await service.update_conversation_title(
            conversation_id=conversation_id,
            user_id=str(current_user.id),
            title=title,
        )

        return ConversationResponse(
            id=conversation.id,
            data_source_id=conversation.data_source_id,
            title=conversation.title,
            is_active=conversation.is_active,
            total_input_tokens=conversation.total_input_tokens,
            total_output_tokens=conversation.total_output_tokens,
            total_cost_usd=float(conversation.total_cost_usd),
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            last_message_at=conversation.last_message_at,
            messages=[],
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# ==================== Usage Tracking Endpoints ====================


@router.get(
    "/usage/summary",
    response_model=UsageSummaryResponse,
    summary="Get usage summary",
    description="Get a summary of LLM usage and costs for the current user.",
)
async def get_usage_summary(
    days: int = Query(30, ge=1, le=365, description="Number of days to include"),
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> UsageSummaryResponse:
    """Get usage summary for the current user."""
    summary = await service.get_user_usage_summary(
        user_id=str(current_user.id),
        days=days,
    )

    return UsageSummaryResponse(
        period_days=summary["period_days"],
        total_input_tokens=summary["total_input_tokens"],
        total_output_tokens=summary["total_output_tokens"],
        total_cost_usd=summary["total_cost_usd"],
        total_calls=summary["total_calls"],
        by_call_type=summary["by_call_type"],
    )
