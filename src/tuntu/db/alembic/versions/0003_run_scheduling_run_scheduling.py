"""run scheduling

Revision ID: 0003_run_scheduling
Revises: 0002_download_tracking
Create Date: 2026-08-13 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_run_scheduling"
down_revision: Union[str, Sequence[str], None] = "0002_download_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False)
        )
    op.execute(
        "UPDATE runs SET status = 'failed', "
        "stats_json = '{\"error_code\":\"process_interrupted_during_upgrade\"}', "
        "finished_at = CURRENT_TIMESTAMP WHERE status = 'running'"
    )
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.create_index(
            "uq_runs_running_profile",
            ["profile_id"],
            unique=True,
            sqlite_where=sa.text("status = 'running'"),
        )


def downgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_index("uq_runs_running_profile")
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.drop_column("enabled")
