"""add metadata-only watchlists

Revision ID: 0006_watchlists
Revises: 0005_content_metadata
Create Date: 2026-08-23 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_watchlists"
down_revision: Union[str, Sequence[str], None] = "0005_content_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlists")),
    )
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("watchlist_id", sa.Integer(), nullable=False),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("source_name", sa.String(length=100), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_watchlist_items_content_item_id_content_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name=op.f("fk_watchlist_items_watchlist_id_watchlists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlist_items")),
        sa.UniqueConstraint(
            "watchlist_id",
            "content_item_id",
            name=op.f("uq_watchlist_items_watchlist_id"),
        ),
    )
    op.create_index(
        op.f("ix_watchlist_items_content_item_id"),
        "watchlist_items",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_watchlist_items_watchlist_id"),
        "watchlist_items",
        ["watchlist_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_watchlist_items_watchlist_id"), table_name="watchlist_items")
    op.drop_index(op.f("ix_watchlist_items_content_item_id"), table_name="watchlist_items")
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
