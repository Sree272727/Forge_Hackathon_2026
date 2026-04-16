"""Service layer for conversation management."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.exceptions.base import NotFoundError
from core.logging import get_logger
from core.models.conversation import (
    Conversation,
    ConversationMessage,
    LLMUsage,
    FileUploadUsage,
    LLMCallType,
)
from core.models.data_source import DataSource

logger = get_logger(__name__)


class ConversationService:
    """Service for managing conversations and tracking LLM usage."""

    def __init__(self, session: AsyncSession):
        """Initialize service with database session."""
        self.session = session

    # ==================== Conversation Management ====================

    async def create_conversation(
        self,
        user_id: str,
        data_source_id: str,
        title: str | None = None,
    ) -> Conversation:
        """Create a new conversation for a data source.

        Args:
            user_id: ID of the user.
            data_source_id: ID of the data source.
            title: Optional title for the conversation.

        Returns:
            Created Conversation instance.
        """
        # Verify data source exists and belongs to user
        await self._verify_data_source_ownership(data_source_id, user_id)

        conversation = Conversation(
            user_id=user_id,
            data_source_id=data_source_id,
            title=title or "New Conversation",
        )

        self.session.add(conversation)
        await self.session.flush()
        await self.session.refresh(conversation)

        logger.info(
            "Conversation created",
            conversation_id=conversation.id,
            user_id=user_id,
            data_source_id=data_source_id,
        )

        return conversation

    async def get_conversation(
        self,
        conversation_id: str,
        user_id: str,
        include_messages: bool = True,
    ) -> Conversation:
        """Get a conversation by ID.

        Args:
            conversation_id: ID of the conversation.
            user_id: ID of the user (for ownership check).
            include_messages: Whether to eagerly load messages.

        Returns:
            Conversation instance.

        Raises:
            NotFoundError: If conversation not found or not owned by user.
        """
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )

        if include_messages:
            stmt = stmt.options(selectinload(Conversation.messages))

        result = await self.session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise NotFoundError(
                message="Conversation not found",
                resource_type="Conversation",
                resource_id=conversation_id,
            )

        return conversation

    async def list_conversations(
        self,
        user_id: str,
        data_source_id: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict], int]:
        """List conversations for a user.

        Args:
            user_id: ID of the user.
            data_source_id: Optional filter by data source.
            skip: Number of records to skip.
            limit: Maximum number of records to return.

        Returns:
            Tuple of (conversations with message_count, total_count).
        """
        from sqlalchemy import func

        base_filter = [Conversation.user_id == user_id]
        if data_source_id:
            base_filter.append(Conversation.data_source_id == data_source_id)

        # Subquery for message count
        message_count_subq = (
            select(func.count(ConversationMessage.id))
            .where(ConversationMessage.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )

        # Get conversations with message count
        stmt = (
            select(
                Conversation,
                message_count_subq.label("message_count"),
            )
            .where(*base_filter)
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        # Convert to dicts with message_count included
        conversations = [
            {
                "conversation": row.Conversation,
                "message_count": row.message_count or 0,
            }
            for row in rows
        ]

        # Get total count
        count_stmt = select(func.count(Conversation.id)).where(*base_filter)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        return conversations, total

    async def update_conversation_title(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
    ) -> Conversation:
        """Update conversation title.

        Args:
            conversation_id: ID of the conversation.
            user_id: ID of the user.
            title: New title.

        Returns:
            Updated Conversation.
        """
        conversation = await self.get_conversation(conversation_id, user_id, include_messages=False)
        conversation.title = title
        await self.session.flush()
        return conversation

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
        conversation = await self.get_conversation(conversation_id, user_id, include_messages=False)
        await self.session.delete(conversation)
        await self.session.flush()

        logger.info("Conversation deleted", conversation_id=conversation_id)

    # ==================== Message Management ====================

    async def add_message(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        code_used: str | None = None,
        execution_time_ms: int | None = None,
        is_error: bool = False,
        error_message: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
        llm_usage_id: str | None = None,
    ) -> ConversationMessage:
        """Add a message to a conversation.

        Args:
            conversation_id: ID of the conversation.
            user_id: ID of the user.
            role: Message role ('user' or 'assistant').
            content: Message content.
            code_used: Code used to generate response (for assistant).
            execution_time_ms: Execution time in milliseconds.
            is_error: Whether the message is an error.
            error_message: Error message if applicable.
            input_tokens: Number of input tokens used.
            output_tokens: Number of output tokens used.
            cost_usd: Cost in USD.
            llm_usage_id: ID of the LLM usage record.

        Returns:
            Created ConversationMessage.
        """
        # Verify ownership
        conversation = await self.get_conversation(conversation_id, user_id, include_messages=False)

        message = ConversationMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            code_used=code_used,
            execution_time_ms=execution_time_ms,
            is_error=is_error,
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            llm_usage_id=llm_usage_id,
        )

        self.session.add(message)

        # Update conversation totals
        conversation.add_cost(input_tokens, output_tokens, cost_usd)

        await self.session.flush()
        await self.session.refresh(message)

        return message

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
            List of ConversationMessage instances.
        """
        # Verify ownership
        await self.get_conversation(conversation_id, user_id, include_messages=False)

        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ==================== LLM Usage Tracking ====================

    async def record_llm_usage(
        self,
        user_id: str,
        call_type: LLMCallType | str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        input_cost_usd: Decimal,
        output_cost_usd: Decimal,
        total_cost_usd: Decimal,
        context: dict | None = None,
        latency_ms: int | None = None,
        success: bool = True,
        error_message: str | None = None,
        metadata: dict | None = None,
    ) -> LLMUsage:
        """Record an LLM API call for tracking.

        Args:
            user_id: ID of the user.
            call_type: Type of LLM call.
            provider: LLM provider name.
            model: Model name.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            input_cost_usd: Input token cost.
            output_cost_usd: Output token cost.
            total_cost_usd: Total cost.
            context: Flexible context dict with relevant IDs based on call_type.
                     Example: {"data_source_id": "...", "conversation_id": "...", "excel_schema_id": "..."}
            latency_ms: Call latency in milliseconds.
            success: Whether the call succeeded.
            error_message: Error message if failed.
            metadata: Additional metadata.

        Returns:
            Created LLMUsage record.
        """
        call_type_str = call_type if isinstance(call_type, str) else call_type.value

        usage = LLMUsage(
            user_id=user_id,
            call_type=call_type_str,
            context=context or {},
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message,
            extra_metadata=metadata or {},
        )

        self.session.add(usage)
        await self.session.flush()
        await self.session.refresh(usage)

        logger.info(
            "LLM usage recorded",
            usage_id=usage.id,
            call_type=call_type_str,
            context=context,
            total_cost=str(total_cost_usd),
            tokens=input_tokens + output_tokens,
        )

        return usage

    async def get_user_usage_summary(
        self,
        user_id: str,
        days: int = 30,
    ) -> dict:
        """Get usage summary for a user.

        Args:
            user_id: ID of the user.
            days: Number of days to include.

        Returns:
            Dictionary with usage statistics.
        """
        from datetime import datetime, timedelta
        from sqlalchemy import func

        since = datetime.utcnow() - timedelta(days=days)

        # Total usage
        total_stmt = select(
            func.sum(LLMUsage.input_tokens).label("total_input"),
            func.sum(LLMUsage.output_tokens).label("total_output"),
            func.sum(LLMUsage.total_cost_usd).label("total_cost"),
            func.count(LLMUsage.id).label("total_calls"),
        ).where(
            LLMUsage.user_id == user_id,
            LLMUsage.created_at >= since,
        )

        result = await self.session.execute(total_stmt)
        totals = result.one()

        # By call type
        by_type_stmt = select(
            LLMUsage.call_type,
            func.sum(LLMUsage.total_cost_usd).label("cost"),
            func.count(LLMUsage.id).label("count"),
        ).where(
            LLMUsage.user_id == user_id,
            LLMUsage.created_at >= since,
        ).group_by(LLMUsage.call_type)

        type_result = await self.session.execute(by_type_stmt)
        by_type = {row.call_type: {"cost": float(row.cost or 0), "count": row.count} for row in type_result}

        return {
            "period_days": days,
            "total_input_tokens": totals.total_input or 0,
            "total_output_tokens": totals.total_output or 0,
            "total_cost_usd": float(totals.total_cost or 0),
            "total_calls": totals.total_calls or 0,
            "by_call_type": by_type,
        }

    # ==================== File Upload Usage ====================

    async def record_file_upload_usage(
        self,
        user_id: str,
        data_source_id: str,
        file_size_bytes: int,
        sheet_count: int,
        metadata_extraction_cost: Decimal = Decimal("0"),
        semantic_mapping_cost: Decimal = Decimal("0"),
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> FileUploadUsage:
        """Record file upload and processing usage.

        Args:
            user_id: ID of the user.
            data_source_id: ID of the data source.
            file_size_bytes: File size in bytes.
            sheet_count: Number of sheets in the file.
            metadata_extraction_cost: Cost of metadata extraction.
            semantic_mapping_cost: Cost of semantic mapping.
            total_input_tokens: Total input tokens used.
            total_output_tokens: Total output tokens used.

        Returns:
            Created FileUploadUsage record.
        """
        total_cost = metadata_extraction_cost + semantic_mapping_cost

        usage = FileUploadUsage(
            user_id=user_id,
            data_source_id=data_source_id,
            file_size_bytes=file_size_bytes,
            sheet_count=sheet_count,
            metadata_extraction_cost_usd=metadata_extraction_cost,
            semantic_mapping_cost_usd=semantic_mapping_cost,
            total_processing_cost_usd=total_cost,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

        self.session.add(usage)
        await self.session.flush()
        await self.session.refresh(usage)

        logger.info(
            "File upload usage recorded",
            usage_id=usage.id,
            data_source_id=data_source_id,
            total_cost=str(total_cost),
        )

        return usage

    # ==================== Helper Methods ====================

    async def _verify_data_source_ownership(
        self,
        data_source_id: str,
        user_id: str,
    ) -> DataSource:
        """Verify data source exists and belongs to user."""
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
