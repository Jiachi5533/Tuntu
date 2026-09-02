"""persist content metadata used by ranking snapshots

Revision ID: 0005_content_metadata
Revises: 0004_run_item_duplicate
Create Date: 2026-08-21 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_content_metadata"
down_revision: Union[str, Sequence[str], None] = "0004_run_item_duplicate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("metadata_json", sa.JSON(), nullable=True))
    op.execute("UPDATE content_items SET metadata_json = '{}' WHERE metadata_json IS NULL")
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.alter_column("metadata_json", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.drop_column("metadata_json")
