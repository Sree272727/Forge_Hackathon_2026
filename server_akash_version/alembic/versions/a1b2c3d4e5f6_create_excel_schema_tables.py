"""Create excel_schemas and query_history tables.

Revision ID: a1b2c3d4e5f6
Revises: 57f5ac9a2f11
Create Date: 2024-01-15 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "57f5ac9a2f11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create excel_schemas and query_history tables."""
    # Create excel_schemas table
    op.create_table(
        "excel_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("manifest", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("semantic_schema", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("workbook_purpose", sa.Text(), nullable=True),
        sa.Column("detected_colors", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("total_sections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_merged_regions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queryable_questions", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("data_quality_notes", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_ready_for_queries", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["data_source_id"],
            ["data_sources.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("data_source_id"),
    )
    op.create_index(
        "ix_excel_schemas_data_source_id",
        "excel_schemas",
        ["data_source_id"],
    )

    # Create query_history table
    op.create_table(
        "query_history",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("excel_schema_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", postgresql.JSON(), nullable=True),
        sa.Column("code_used", sa.Text(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("iterations_used", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["excel_schema_id"],
            ["excel_schemas.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_query_history_excel_schema_id",
        "query_history",
        ["excel_schema_id"],
    )
    op.create_index(
        "ix_query_history_user_id",
        "query_history",
        ["user_id"],
    )


def downgrade() -> None:
    """Drop excel_schemas and query_history tables."""
    op.drop_index("ix_query_history_user_id", table_name="query_history")
    op.drop_index("ix_query_history_excel_schema_id", table_name="query_history")
    op.drop_table("query_history")

    op.drop_index("ix_excel_schemas_data_source_id", table_name="excel_schemas")
    op.drop_table("excel_schemas")
