from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class UserRow(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class SessionRow(TimestampMixin, Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SetupTokenRow(TimestampMixin, Base):
    __tablename__ = "setup_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class ProfileRow(TimestampMixin, Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    destination_subdir: Mapped[str] = mapped_column(String(500), nullable=False)
    top_n: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default="20")
    daily_time: Mapped[str | None] = mapped_column(String(8))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class ProfileSourceRow(Base):
    __tablename__ = "profile_sources"
    __table_args__ = (
        UniqueConstraint("profile_id", "source_kind", "source_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")


class WatchlistRow(TimestampMixin, Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    query: Mapped[str] = mapped_column(String(300), nullable=False)
    aliases_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    automation_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class WatchlistItemRow(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "content_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index(
            "uq_runs_running_profile",
            "profile_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunSourceResultRow(Base):
    __tablename__ = "run_source_results"
    __table_args__ = (UniqueConstraint("run_id", "source_kind", "source_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_summary: Mapped[str | None] = mapped_column(Text)


class ContentItemRow(TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (UniqueConstraint("namespace", "normalized_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_key: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RunItemRow(Base):
    __tablename__ = "run_items"
    __table_args__ = (UniqueConstraint("run_id", "content_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)
    rankings_json: Mapped[list] = mapped_column(JSON, nullable=False)
    duplicate_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class CandidateRow(TimestampMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(
            "length(btih) = 40 AND btih = lower(btih) "
            "AND btih NOT GLOB '*[^0-9a-f]*'",
            name="normalized_btih",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    btih: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)


class CandidateEvidenceRow(TimestampMixin, Base):
    __tablename__ = "candidate_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_item_id: Mapped[int] = mapped_column(
        ForeignKey("run_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    magnet_uri: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    size_mb: Mapped[float | None] = mapped_column(Float)
    seeders: Mapped[int | None] = mapped_column(Integer)
    chinese_subtitles: Mapped[str] = mapped_column(String(10), nullable=False)
    uncensored: Mapped[str] = mapped_column(String(10), nullable=False)
    uhd: Mapped[str] = mapped_column(String(10), nullable=False)
    notes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class EvaluationRow(TimestampMixin, Base):
    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("run_item_id", "candidate_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_item_id: Mapped[int] = mapped_column(
        ForeignKey("run_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class DownloadTaskRow(TimestampMixin, Base):
    __tablename__ = "download_tasks"
    __table_args__ = (
        UniqueConstraint(
            "download_client_key",
            "content_item_id",
            "idempotency_generation",
            name="uq_download_tasks_client_content_generation",
        ),
        UniqueConstraint(
            "download_client_key",
            "candidate_id",
            "idempotency_generation",
            name="uq_download_tasks_client_candidate_generation",
        ),
        Index("ix_download_tasks_status_updated", "status", "updated_at"),
        Index(
            "uq_download_tasks_destination_ownership",
            "download_client_key",
            "destination_path",
            unique=True,
            sqlite_where=text(
                "destination_path IS NOT NULL AND ownership_acquired = 0 "
                "AND status IN ('submitting', 'submitted', 'downloading', 'attention_required')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    download_client_key: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_items.id", ondelete="RESTRICT"), index=True
    )
    supersedes_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("download_tasks.id", ondelete="RESTRICT"), index=True
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    destination_path: Mapped[str | None] = mapped_column(String(1000))
    ownership_acquired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    external_reference: Mapped[str | None] = mapped_column(Text)
    baseline_json: Mapped[dict | None] = mapped_column(JSON)
    completion_state_json: Mapped[dict | None] = mapped_column(JSON)
    attention_after_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_summary: Mapped[str | None] = mapped_column(Text)
    manual_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class DownloadEventRow(Base):
    __tablename__ = "download_events"
    __table_args__ = (UniqueConstraint("download_task_id", "sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    download_task_id: Mapped[str] = mapped_column(
        ForeignKey("download_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceHealthRow(Base):
    __tablename__ = "source_health"

    source_kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_result_count: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_summary: Mapped[str | None] = mapped_column(Text)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
