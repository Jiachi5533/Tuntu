"""link deduplicated run items to the original download

Revision ID: 0004_run_item_duplicate
Revises: 0003_run_scheduling
Create Date: 2026-08-13 23:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_run_item_duplicate"
down_revision: Union[str, Sequence[str], None] = "0003_run_scheduling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("run_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("duplicate_task_id", sa.String(length=36), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("run_items", schema=None) as batch_op:
        batch_op.drop_column("duplicate_task_id")
