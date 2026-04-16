"""Service layer for Excel agent operations."""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agents.orchestrator import ExcelAgentOrchestrator, get_orchestrator
from core.agents.extractor import manifest_to_dict
from core.agents.mapper import schema_to_dict
from core.agents.semantic_enricher import enrichment_to_dict
from core.agents.token_tracker import calculate_cost, estimate_tokens
from core.config import settings
from core.exceptions.base import NotFoundError, ValidationError, BusinessLogicError
from core.logging import get_logger
from core.models.data_source import DataSource
from core.models.excel_schema import ExcelSchema, QueryHistory, ProcessingStatus
from core.models.conversation import (
    Conversation,
    ConversationMessage,
    LLMUsage,
    LLMCallType,
)
from core.services.conversation import ConversationService

logger = get_logger(__name__)


class ExcelAgentService:
    """Service for managing Excel agent operations.

    Handles:
    - Processing Excel files through the agent pipeline
    - Storing and retrieving schemas from database
    - Executing queries against Excel files
    - Query history tracking
    """

    def __init__(self, session: AsyncSession):
        """Initialize service with database session."""
        self.session = session
        self.orchestrator = get_orchestrator()

    async def process_data_source(
        self,
        data_source_id: str,
        user_id: str,
        force_reprocess: bool = False,
    ) -> ExcelSchema:
        """Process a data source through the Excel agent pipeline.

        Args:
            data_source_id: ID of the data source to process.
            user_id: ID of the user making the request.
            force_reprocess: Force reprocessing even if already processed.

        Returns:
            ExcelSchema with processing results.

        Raises:
            NotFoundError: If data source not found.
            ValidationError: If file is not accessible.
        """
        # Get data source
        data_source = await self._get_data_source(data_source_id, user_id)

        # Check if already processed
        existing_schema = await self._get_existing_schema(data_source_id)

        if existing_schema and not force_reprocess:
            if existing_schema.processing_status == ProcessingStatus.COMPLETED:
                logger.info(
                    "Data source already processed",
                    data_source_id=data_source_id,
                    schema_id=existing_schema.id,
                )
                return existing_schema
            elif existing_schema.processing_status in (
                ProcessingStatus.EXTRACTING,
                ProcessingStatus.MAPPING,
                ProcessingStatus.ENRICHING,
            ):
                logger.info(
                    "Processing already in progress",
                    data_source_id=data_source_id,
                )
                return existing_schema

        # Create or update schema record
        if existing_schema:
            schema = existing_schema
        else:
            schema = ExcelSchema(data_source_id=data_source_id)
            self.session.add(schema)

        # Verify file exists
        file_path = data_source.stored_file_path
        if not Path(file_path).exists():
            schema.mark_failed(f"File not found: {file_path}")
            await self.session.flush()
            raise ValidationError(
                f"Stored file not found: {file_path}",
                field="stored_file_path",
            )

        try:
            # Mark as extracting
            schema.mark_extracting()
            await self.session.flush()

            # Process through orchestrator
            result = await self.orchestrator.process_workbook(
                file_path=file_path,
                force_reprocess=force_reprocess,
            )

            if not result.success:
                schema.mark_failed(result.error or "Unknown error")
                await self.session.flush()
                raise BusinessLogicError(
                    message=f"Processing failed: {result.error}",
                    code="PROCESSING_FAILED",
                )

            processed = result.data

            # Extract all queryable questions from enrichment or schema
            all_questions = []
            if processed.enrichment:
                for sheet in processed.enrichment.sheets.values():
                    all_questions.extend(sheet.answerable_question_types)
            elif processed.schema:
                for sheet in processed.schema.sheets.values():
                    all_questions.extend(sheet.queryable_questions)

            # Extract data quality notes from enrichment or schema
            data_quality_notes = []
            if processed.enrichment:
                for sheet in processed.enrichment.sheets.values():
                    for flag in sheet.data_quality_flags:
                        data_quality_notes.append(f"{sheet.sheet_name}: {flag.description}")
            elif processed.schema:
                data_quality_notes = processed.schema.data_quality_notes

            # Get workbook purpose from enrichment or schema
            workbook_purpose = None
            workbook_title = None
            domain = None
            context_header = None
            query_routing = {}

            if processed.enrichment:
                workbook_purpose = processed.enrichment.workbook_purpose
                workbook_title = processed.enrichment.workbook_title
                domain = processed.enrichment.domain
                context_header = processed.enrichment.context_header_for_qa
                query_routing = {
                    "schema_questions": processed.enrichment.recommended_query_routing.schema_questions,
                    "financial_questions": processed.enrichment.recommended_query_routing.financial_questions,
                    "trend_questions": processed.enrichment.recommended_query_routing.trend_questions,
                    "lookup_questions": processed.enrichment.recommended_query_routing.lookup_questions,
                }
            elif processed.schema:
                workbook_purpose = processed.schema.workbook_purpose

            # Mark as completed
            schema.mark_completed(
                manifest=processed.manifest_dict,
                semantic_schema=processed.schema_dict,
                workbook_purpose=workbook_purpose,
                detected_colors=processed.manifest.detected_colors if processed.manifest else [],
                total_sections=processed.manifest.total_sections if processed.manifest else 0,
                total_merged_regions=processed.manifest.total_merged_regions if processed.manifest else 0,
                queryable_questions=all_questions,
                data_quality_notes=data_quality_notes,
                enrichment=processed.enrichment_dict if processed.enrichment else {},
                workbook_title=workbook_title,
                domain=domain,
                context_header_for_qa=context_header,
                query_routing=query_routing,
            )

            await self.session.flush()
            await self.session.refresh(schema)

            logger.info(
                "Data source processed successfully",
                data_source_id=data_source_id,
                schema_id=schema.id,
                is_ready=schema.is_ready_for_queries,
            )

            return schema

        except BusinessLogicError:
            raise
        except Exception as e:
            logger.error(
                "Processing failed with exception",
                data_source_id=data_source_id,
                error=str(e),
                exc_info=True,
            )
            schema.mark_failed(str(e))
            await self.session.flush()
            raise BusinessLogicError(
                message=f"Processing failed: {str(e)}",
                code="PROCESSING_ERROR",
            )

    async def get_schema(
        self,
        data_source_id: str,
        user_id: str,
    ) -> ExcelSchema:
        """Get the Excel schema for a data source.

        Args:
            data_source_id: ID of the data source.
            user_id: ID of the user making the request.

        Returns:
            ExcelSchema if exists.

        Raises:
            NotFoundError: If schema not found.
        """
        # Verify user owns the data source
        await self._get_data_source(data_source_id, user_id)

        schema = await self._get_existing_schema(data_source_id)
        if not schema:
            raise NotFoundError(
                message="Schema not found. Process the data source first.",
                resource_type="ExcelSchema",
                resource_id=data_source_id,
            )

        return schema

    async def ask_question(
        self,
        data_source_id: str,
        user_id: str,
        question: str,
        conversation_id: str | None = None,
    ) -> dict:
        """Ask a natural language question about an Excel file.

        Args:
            data_source_id: ID of the data source.
            user_id: ID of the user making the request.
            question: Natural language question.
            conversation_id: Optional ID of existing conversation.

        Returns:
            Dict containing answer, code used, and metadata.

        Raises:
            NotFoundError: If data source or schema not found.
            BusinessLogicError: If not ready for queries.
        """
        # Get data source and schema
        data_source = await self._get_data_source(data_source_id, user_id)
        schema = await self._get_existing_schema(data_source_id)

        if not schema:
            raise NotFoundError(
                message="Schema not found. Process the data source first.",
                resource_type="ExcelSchema",
                resource_id=data_source_id,
            )

        if not schema.is_ready_for_queries:
            raise BusinessLogicError(
                message=f"Data source not ready for queries. Status: {schema.processing_status}",
                code="NOT_READY",
            )

        # Get or create conversation
        conv_service = ConversationService(self.session)

        if conversation_id:
            conversation = await conv_service.get_conversation(
                conversation_id, user_id, include_messages=False
            )
        else:
            # Create new conversation with first question as title
            title = question[:100] + ("..." if len(question) > 100 else "")
            conversation = await conv_service.create_conversation(
                user_id=user_id,
                data_source_id=data_source_id,
                title=title,
            )

        # Add user message to conversation
        user_message = await conv_service.add_message(
            conversation_id=conversation.id,
            user_id=user_id,
            role="user",
            content=question,
        )

        # Create query history record (for backwards compatibility)
        query_history = QueryHistory(
            excel_schema_id=schema.id,
            user_id=user_id,
            question=question,
        )
        self.session.add(query_history)
        await self.session.flush()  # Ensure query_history.id is available

        start_time = time.time()

        try:
            # Execute query through orchestrator
            result = await self.orchestrator.ask_question(
                question=question,
                file_path=data_source.stored_file_path,
                schema=schema.semantic_schema,
            )

            execution_time_ms = int((time.time() - start_time) * 1000)

            # Estimate token usage (rough estimate based on input/output)
            input_tokens = estimate_tokens(question + str(schema.semantic_schema)[:2000])
            output_tokens = 0
            if result.success and result.data:
                output_content = str(result.data.get("answer", "")) + str(result.data.get("code_used", ""))
                output_tokens = estimate_tokens(output_content)

            # Calculate cost
            usage = calculate_cost(
                settings.AGENT_LLM_PROVIDER,
                settings.AGENT_LLM_MODEL,
                input_tokens,
                output_tokens,
            )

            # Record LLM usage with flexible context
            llm_usage = await conv_service.record_llm_usage(
                user_id=user_id,
                call_type=LLMCallType.ASK_QUESTION,
                provider=settings.AGENT_LLM_PROVIDER,
                model=settings.AGENT_LLM_MODEL,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                input_cost_usd=usage.input_cost_usd,
                output_cost_usd=usage.output_cost_usd,
                total_cost_usd=usage.total_cost_usd,
                context={
                    "data_source_id": data_source_id,
                    "conversation_id": conversation.id,
                    "excel_schema_id": schema.id,
                    "query_id": query_history.id,
                },
                latency_ms=execution_time_ms,
                success=result.success,
                error_message=result.error if not result.success else None,
            )

            if result.success:
                query_history.success = True
                query_history.answer = result.data.get("answer")
                query_history.code_used = result.data.get("code_used")
                query_history.iterations_used = result.data.get("iterations", 1)

                # Add assistant message to conversation
                answer_content = self._format_answer(result.data.get("answer"))
                await conv_service.add_message(
                    conversation_id=conversation.id,
                    user_id=user_id,
                    role="assistant",
                    content=answer_content,
                    code_used=result.data.get("code_used"),
                    execution_time_ms=execution_time_ms,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.total_cost_usd,
                    llm_usage_id=llm_usage.id,
                )
            else:
                query_history.success = False
                query_history.error_message = result.error

                # Add error message to conversation
                await conv_service.add_message(
                    conversation_id=conversation.id,
                    user_id=user_id,
                    role="assistant",
                    content=f"Error: {result.error}",
                    is_error=True,
                    error_message=result.error,
                    execution_time_ms=execution_time_ms,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.total_cost_usd,
                    llm_usage_id=llm_usage.id,
                )

            query_history.execution_time_ms = execution_time_ms
            await self.session.flush()

            logger.info(
                "Query executed",
                data_source_id=data_source_id,
                conversation_id=conversation.id,
                question=question[:100],
                success=result.success,
                execution_time_ms=execution_time_ms,
                cost_usd=str(usage.total_cost_usd),
            )

            return {
                "success": result.success,
                "answer": result.data.get("answer") if result.success else None,
                "code_used": result.data.get("code_used") if result.success else None,
                "iterations": result.data.get("iterations", 1) if result.success else None,
                "error": result.error if not result.success else None,
                "execution_time_ms": execution_time_ms,
                "query_id": query_history.id,
                "conversation_id": conversation.id,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": float(usage.total_cost_usd),
            }

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            query_history.success = False
            query_history.error_message = str(e)
            query_history.execution_time_ms = execution_time_ms
            await self.session.flush()

            logger.error(
                "Query failed",
                data_source_id=data_source_id,
                question=question[:100],
                error=str(e),
                exc_info=True,
            )

            return {
                "success": False,
                "answer": None,
                "error": str(e),
                "execution_time_ms": execution_time_ms,
                "query_id": query_history.id,
                "conversation_id": conversation.id if conversation else None,
            }

    def _format_answer(self, answer: any) -> str:
        """Format answer for storage."""
        if answer is None:
            return "No result"
        if isinstance(answer, str):
            return answer
        if isinstance(answer, (int, float)):
            return str(answer)
        return str(answer)

    async def get_query_history(
        self,
        data_source_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[QueryHistory]:
        """Get query history for a data source.

        Args:
            data_source_id: ID of the data source.
            user_id: ID of the user making the request.
            limit: Maximum number of queries to return.

        Returns:
            List of QueryHistory records.
        """
        # Verify user owns the data source
        await self._get_data_source(data_source_id, user_id)

        schema = await self._get_existing_schema(data_source_id)
        if not schema:
            return []

        stmt = (
            select(QueryHistory)
            .where(QueryHistory.excel_schema_id == schema.id)
            .where(QueryHistory.user_id == user_id)
            .order_by(QueryHistory.created_at.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_suggested_questions(
        self,
        data_source_id: str,
        user_id: str,
    ) -> list[str]:
        """Get suggested questions for a data source.

        Args:
            data_source_id: ID of the data source.
            user_id: ID of the user making the request.

        Returns:
            List of suggested questions.
        """
        schema = await self.get_schema(data_source_id, user_id)
        return schema.queryable_questions or []

    # ==================== Conversation Methods ====================

    async def get_conversations(
        self,
        user_id: str,
        data_source_id: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[Conversation], int]:
        """Get conversations for a user.

        Args:
            user_id: ID of the user.
            data_source_id: Optional filter by data source.
            skip: Number of records to skip.
            limit: Maximum records to return.

        Returns:
            Tuple of (conversations, total_count).
        """
        conv_service = ConversationService(self.session)
        return await conv_service.list_conversations(
            user_id=user_id,
            data_source_id=data_source_id,
            skip=skip,
            limit=limit,
        )

    async def get_conversation(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation:
        """Get a conversation by ID.

        Args:
            conversation_id: ID of the conversation.
            user_id: ID of the user.

        Returns:
            Conversation with messages.
        """
        conv_service = ConversationService(self.session)
        return await conv_service.get_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            include_messages=True,
        )

    async def get_conversation_messages(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 100,
    ) -> list[ConversationMessage]:
        """Get messages for a conversation.

        Args:
            conversation_id: ID of the conversation.
            user_id: ID of the user.
            limit: Maximum messages to return.

        Returns:
            List of messages.
        """
        conv_service = ConversationService(self.session)
        return await conv_service.get_conversation_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
        )

    async def delete_conversation(
        self,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Delete a conversation.

        Args:
            conversation_id: ID of the conversation.
            user_id: ID of the user.
        """
        conv_service = ConversationService(self.session)
        await conv_service.delete_conversation(conversation_id, user_id)

    async def get_usage_summary(
        self,
        user_id: str,
        days: int = 30,
    ) -> dict:
        """Get usage summary for a user.

        Args:
            user_id: ID of the user.
            days: Number of days to include.

        Returns:
            Usage statistics dictionary.
        """
        conv_service = ConversationService(self.session)
        return await conv_service.get_user_usage_summary(user_id, days)

    async def _get_data_source(
        self,
        data_source_id: str,
        user_id: str,
    ) -> DataSource:
        """Get and verify data source ownership."""
        stmt = select(DataSource).where(
            DataSource.id == data_source_id,
            DataSource.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        data_source = result.scalar_one_or_none()

        if not data_source:
            raise NotFoundError(
                message="Data source not found",
                resource_type="DataSource",
                resource_id=data_source_id,
            )

        return data_source

    async def _get_existing_schema(
        self,
        data_source_id: str,
    ) -> ExcelSchema | None:
        """Get existing schema for a data source."""
        stmt = select(ExcelSchema).where(ExcelSchema.data_source_id == data_source_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
