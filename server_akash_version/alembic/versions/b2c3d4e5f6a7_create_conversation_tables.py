"""Create conversation and LLM usage tracking tables.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2024-01-15 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create conversations, conversation_messages, llm_usage, and file_upload_usage tables."""

    # Create conversations table
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="New Conversation"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_data_source_id", "conversations", ["data_source_id"])

    # Create llm_usage table (before conversation_messages due to FK)
    # Uses flexible JSON 'context' column instead of individual ID columns
    op.create_table(
        "llm_usage",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("call_type", sa.String(50), nullable=False),
        # Flexible context column - stores relevant IDs based on call_type
        # Example: {"data_source_id": "uuid", "conversation_id": "uuid", "excel_schema_id": "uuid"}
        sa.Column("context", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("output_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        # Optional additional metadata
        sa.Column("metadata", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_llm_usage_user_id", "llm_usage", ["user_id"])
    op.create_index("ix_llm_usage_call_type", "llm_usage", ["call_type"])

    # Create conversation_messages table
    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("code_used", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("llm_usage_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["llm_usage_id"], ["llm_usage.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"])

    # Create file_upload_usage table
    op.create_table(
        "file_upload_usage",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("sheet_count", sa.Integer(), nullable=False),
        sa.Column("metadata_extraction_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("semantic_mapping_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("total_processing_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_file_upload_usage_user_id", "file_upload_usage", ["user_id"])
    op.create_index("ix_file_upload_usage_data_source_id", "file_upload_usage", ["data_source_id"])


def downgrade() -> None:
    """Drop all conversation and LLM usage tables."""
    op.drop_index("ix_file_upload_usage_data_source_id", table_name="file_upload_usage")
    op.drop_index("ix_file_upload_usage_user_id", table_name="file_upload_usage")
    op.drop_table("file_upload_usage")

    op.drop_index("ix_conversation_messages_conversation_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")

    op.drop_index("ix_llm_usage_call_type", table_name="llm_usage")
    op.drop_index("ix_llm_usage_user_id", table_name="llm_usage")
    op.drop_table("llm_usage")

    op.drop_index("ix_conversations_data_source_id", table_name="conversations")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
