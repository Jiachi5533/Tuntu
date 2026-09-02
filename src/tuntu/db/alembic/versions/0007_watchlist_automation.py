"""add watchlist automation settings

Revision ID: 0007_watchlist_automation
Revises: 0006_watchlists
Create Date: 2026-08-25 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_watchlist_automation"
down_revision: Union[str, Sequence[str], None] = "0006_watchlists"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watchlists",
        sa.Column(
            "automation_json",
            sa.JSON(),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("watchlists", "automation_json")
