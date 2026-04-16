"""Add semantic enrichment fields to excel_schemas.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2024-01-16 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add semantic enrichment fields to excel_schemas table."""
    # Add enrichment JSON column for full semantic enrichment data
    op.add_column(
        "excel_schemas",
        sa.Column("enrichment", postgresql.JSON(), nullable=False, server_default="{}"),
    )

    # Add workbook_title column
    op.add_column(
        "excel_schemas",
        sa.Column("workbook_title", sa.Text(), nullable=True),
    )

    # Add domain column (financial, sales_crm, operations_inventory, general, mixed)
    op.add_column(
        "excel_schemas",
        sa.Column("domain", sa.String(50), nullable=True, server_default="general"),
    )

    # Add context_header_for_qa - critical for Q&A prompt injection
    op.add_column(
        "excel_schemas",
        sa.Column("context_header_for_qa", sa.Text(), nullable=True),
    )

    # Add query_routing JSON column for routing recommendations
    op.add_column(
        "excel_schemas",
        sa.Column("query_routing", postgresql.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    """Remove semantic enrichment fields from excel_schemas table."""
    op.drop_column("excel_schemas", "query_routing")
    op.drop_column("excel_schemas", "context_header_for_qa")
    op.drop_column("excel_schemas", "domain")
    op.drop_column("excel_schemas", "workbook_title")
    op.drop_column("excel_schemas", "enrichment")
