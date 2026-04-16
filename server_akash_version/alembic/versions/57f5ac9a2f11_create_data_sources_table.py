"""create_data_sources_table

Revision ID: 57f5ac9a2f11
Revises: dce8846e5d56
Create Date: 2026-04-13 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "57f5ac9a2f11"
down_revision: Union[str, Sequence[str], None] = "dce8846e5d56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "data_sources",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("stored_file_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("file_extension", sa.String(length=20), nullable=False),
        sa.Column("file_size_bytes", sa.BIGINT(), nullable=False),
        sa.Column("sheet_count", sa.Integer(), nullable=False),
        sa.Column("sheet_names", sa.JSON(), nullable=False),
        sa.Column("file_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("meta_info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_data_sources_user_id"), "data_sources", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_data_sources_user_id"), table_name="data_sources")
    op.drop_table("data_sources")
