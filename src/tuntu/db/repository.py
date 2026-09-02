from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from tuntu.models import CandidateEvidence, RuleReason, TruthValue

from .database import Database
from .models import (
    AuditEventRow,
    CandidateEvidenceRow,
    CandidateRow,
    ContentItemRow,
    DownloadEventRow,
    DownloadTaskRow,
    EvaluationRow,
    ProfileRow,
    RunItemRow,
    RunRow,
    RunSourceResultRow,
    SettingRow,
    SourceHealthRow,
    WatchlistItemRow,
    WatchlistRow,
)


class IdempotencyConflict(RuntimeError):
    pass


class DestinationBusy(RuntimeError):
    pass


class RunAlreadyActive(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    id: int
    name: str
    settings: dict
    destination_subdir: str
    top_n: int
    daily_time: str | None
    enabled: bool
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    profile_id: int
    status: str
    trigger: str
    config_snapshot: dict
    stats: dict
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunSourceResultRecord:
    source_kind: str
    source_name: str
    status: str
    latency_ms: int | None
    result_count: int | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class DownloadEventRecord:
    sequence: int
    status: str
    source: str
    evidence: dict
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadTaskRecord:
    id: str
    status: str
    download_client_key: str
    profile_id: int
    run_item_id: int | None
    content_item_id: int
    candidate_id: int
    btih: str
    generation: int
    supersedes_task_id: str | None
    destination_path: str | None
    baseline: object | None
    completion_state: object | None
    attention_after_at: datetime | None
    external_reference: str | None
    last_error_code: str | None
    last_error_summary: str | None
    ownership_acquired: bool
    manual_completed: bool


@dataclass(frozen=True, slots=True)
class SourceHealthRecord:
    source_kind: str
    source_name: str
    last_success_at: datetime | None
    last_checked_at: datetime | None
    last_latency_ms: int | None
    last_result_count: int | None
    consecutive_failures: int
    last_error_code: str | None
    last_error_summary: str | None


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def create_profile(
        self,
        name: str,
        settings: dict,
        *,
        destination_subdir: str,
        top_n: int = 20,
        daily_time: str | None = None,
        enabled: bool = True,
    ) -> int:
        with self.database.session() as session:
            row = ProfileRow(
                name=name,
                settings_json=copy.deepcopy(settings),
                destination_subdir=destination_subdir,
                top_n=top_n,
                daily_time=daily_time,
                enabled=enabled,
            )
            session.add(row)
            session.flush()
            return row.id

    def get_setting(self, key: str) -> dict | None:
        with self.database.session() as session:
            row = session.get(SettingRow, key)
            return copy.deepcopy(row.value_json) if row is not None else None

    def set_setting(self, key: str, value: dict) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            session.execute(
                sqlite_insert(SettingRow)
                .values(
                    key=key,
                    value_json=copy.deepcopy(value),
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={
                        "value_json": copy.deepcopy(value),
                        "updated_at": now,
                    },
                )
            )

    def create_watchlist(
        self, name: str, subject_type: str, query: str, aliases: list[str]
    ) -> int:
        with self.database.session() as session:
            row = WatchlistRow(
                name=name,
                subject_type=subject_type,
                query=query,
                aliases_json=copy.deepcopy(aliases),
                automation_json={},
            )
            session.add(row)
            session.flush()
            return row.id

    def list_watchlists(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.execute(
                select(WatchlistRow, func.count(WatchlistItemRow.id))
                .outerjoin(
                    WatchlistItemRow,
                    WatchlistItemRow.watchlist_id == WatchlistRow.id,
                )
                .group_by(WatchlistRow.id)
                .order_by(WatchlistRow.updated_at.desc(), WatchlistRow.id.desc())
            ).all()
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "subject_type": row.subject_type,
                    "query": row.query,
                    "aliases": copy.deepcopy(row.aliases_json),
                    "automation": copy.deepcopy(row.automation_json or {}),
                    "item_count": count,
                    "created_at": self._as_utc(row.created_at),
                    "updated_at": self._as_utc(row.updated_at),
                }
                for row, count in rows
            ]

    def get_watchlist_detail(self, watchlist_id: int) -> dict | None:
        with self.database.session() as session:
            watchlist = session.get(WatchlistRow, watchlist_id)
            if watchlist is None:
                return None
            rows = session.execute(
                select(WatchlistItemRow, ContentItemRow)
                .join(
                    ContentItemRow,
                    ContentItemRow.id == WatchlistItemRow.content_item_id,
                )
                .where(WatchlistItemRow.watchlist_id == watchlist_id)
                .order_by(ContentItemRow.normalized_key, ContentItemRow.id)
            ).all()
            content_ids = [content.id for _, content in rows]
            latest_downloads = {}
            if content_ids:
                downloads = session.scalars(
                    select(DownloadTaskRow)
                    .where(DownloadTaskRow.content_item_id.in_(content_ids))
                    .order_by(
                        DownloadTaskRow.updated_at.desc(),
                        DownloadTaskRow.created_at.desc(),
                    )
                ).all()
                for download in downloads:
                    latest_downloads.setdefault(
                        download.content_item_id, self._download_row_dict(download)
                    )
            return {
                "id": watchlist.id,
                "name": watchlist.name,
                "subject_type": watchlist.subject_type,
                "query": watchlist.query,
                "aliases": copy.deepcopy(watchlist.aliases_json),
                "automation": copy.deepcopy(watchlist.automation_json or {}),
                "created_at": self._as_utc(watchlist.created_at),
                "updated_at": self._as_utc(watchlist.updated_at),
                "items": [
                    {
                        "id": item.id,
                        "content_item_id": content.id,
                        "namespace": content.namespace,
                        "raw_key": content.raw_key,
                        "normalized_key": content.normalized_key,
                        "title": content.title,
                        "metadata": copy.deepcopy(content.metadata_json or {}),
                        "state": item.state,
                        "source_name": item.source_name,
                        "discovered_at": self._as_utc(item.discovered_at),
                        "updated_at": self._as_utc(item.updated_at),
                        "download": latest_downloads.get(content.id),
                    }
                    for item, content in rows
                ],
            }

    def update_watchlist_automation(
        self, watchlist_id: int, automation: dict
    ) -> None:
        with self.database.session() as session:
            row = session.get(WatchlistRow, watchlist_id)
            if row is None:
                raise KeyError(watchlist_id)
            row.automation_json = copy.deepcopy(automation)
            row.updated_at = datetime.now(UTC)

    def list_schedulable_watchlists(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.scalars(
                select(WatchlistRow).order_by(WatchlistRow.id)
            ).all()
            return [
                {
                    "id": row.id,
                    "automation": copy.deepcopy(row.automation_json or {}),
                }
                for row in rows
                if (row.automation_json or {}).get("enabled") is True
            ]

    def add_watchlist_item(
        self,
        watchlist_id: int,
        content_item_id: int,
        *,
        source_name: str,
    ) -> bool:
        now = datetime.now(UTC)
        with self.database.session() as session:
            result = session.execute(
                sqlite_insert(WatchlistItemRow)
                .values(
                    watchlist_id=watchlist_id,
                    content_item_id=content_item_id,
                    state="pending",
                    source_name=source_name,
                    discovered_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["watchlist_id", "content_item_id"]
                )
            )
            row = session.get(WatchlistRow, watchlist_id)
            if row is not None:
                row.updated_at = now
            return result.rowcount == 1

    def set_watchlist_item_state(
        self, watchlist_id: int, content_item_id: int, state: str
    ) -> bool:
        with self.database.session() as session:
            row = session.scalar(
                select(WatchlistItemRow).where(
                    WatchlistItemRow.watchlist_id == watchlist_id,
                    WatchlistItemRow.content_item_id == content_item_id,
                )
            )
            if row is None:
                return False
            row.state = state
            row.updated_at = datetime.now(UTC)
            return True

    def get_watchlist_item(
        self, watchlist_id: int, content_item_id: int
    ) -> dict | None:
        detail = self.get_watchlist_detail(watchlist_id)
        if detail is None:
            return None
        return next(
            (
                item
                for item in detail["items"]
                if item["content_item_id"] == content_item_id
            ),
            None,
        )

    def get_profile(self, profile_id: int) -> ProfileRecord | None:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            return self._profile_record(row) if row is not None else None

    def list_profiles(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        include_archived: bool = False,
    ) -> tuple[list[ProfileRecord], int]:
        conditions = [] if include_archived else [ProfileRow.archived_at.is_(None)]
        with self.database.session() as session:
            total = session.scalar(
                select(func.count()).select_from(ProfileRow).where(*conditions)
            ) or 0
            rows = session.scalars(
                select(ProfileRow)
                .where(*conditions)
                .order_by(ProfileRow.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._profile_record(row) for row in rows], total

    def update_profile(
        self,
        profile_id: int,
        *,
        name: str,
        settings: dict,
        destination_subdir: str,
        top_n: int,
        daily_time: str | None,
        enabled: bool,
    ) -> None:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            row.name = name
            row.settings_json = copy.deepcopy(settings)
            row.destination_subdir = destination_subdir
            row.top_n = top_n
            row.daily_time = daily_time
            row.enabled = enabled
            row.updated_at = datetime.now(UTC)

    def restore_profile(self, profile_id: int) -> None:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            row.archived_at = None
            row.updated_at = datetime.now(UTC)

    def list_schedulable_profiles(self) -> list[ProfileRecord]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ProfileRow)
                .where(
                    ProfileRow.enabled.is_(True),
                    ProfileRow.archived_at.is_(None),
                    ProfileRow.daily_time.is_not(None),
                )
                .order_by(ProfileRow.id)
            ).all()
            return [self._profile_record(row) for row in rows]

    def set_profile_enabled(self, profile_id: int, enabled: bool) -> None:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            row.enabled = enabled
            row.updated_at = datetime.now(UTC)

    def update_profile_settings(self, profile_id: int, settings: dict) -> None:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            row.settings_json = copy.deepcopy(settings)
            row.updated_at = datetime.now(UTC)

    def archive_profile(self, profile_id: int) -> None:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            row.archived_at = datetime.now(UTC)
            row.updated_at = row.archived_at

    def is_profile_archived(self, profile_id: int) -> bool:
        with self.database.session() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            return row.archived_at is not None

    def create_run(self, profile_id: int, config_snapshot: dict, *, trigger: str) -> str:
        run_id = str(uuid.uuid4())
        try:
            with self.database.session() as session:
                session.add(
                    RunRow(
                        id=run_id,
                        profile_id=profile_id,
                        status="running",
                        trigger=trigger,
                        config_snapshot=copy.deepcopy(config_snapshot),
                        stats_json={},
                    )
                )
                session.flush()
        except IntegrityError as exc:
            if "runs.profile_id" in str(exc).casefold():
                raise RunAlreadyActive("profile already has a running run") from exc
            raise
        return run_id

    def finish_run(self, run_id: str, status: str, stats: dict) -> None:
        if status not in {"success", "partial", "failed"}:
            raise ValueError("invalid terminal run status")
        with self.database.session() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            if row.status != "running":
                raise ValueError("run is already terminal")
            row.status = status
            row.stats_json = copy.deepcopy(stats)
            row.finished_at = datetime.now(UTC)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self.database.session() as session:
            row = session.get(RunRow, run_id)
            return self._run_record(row) if row is not None else None

    def list_runs(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        profile_id: int | None = None,
    ) -> tuple[list[dict], int]:
        conditions = [] if profile_id is None else [RunRow.profile_id == profile_id]
        with self.database.session() as session:
            total = session.scalar(
                select(func.count()).select_from(RunRow).where(*conditions)
            ) or 0
            rows = session.execute(
                select(RunRow, ProfileRow.name)
                .join(ProfileRow, ProfileRow.id == RunRow.profile_id)
                .where(*conditions)
                .order_by(RunRow.started_at.desc(), RunRow.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [
                {
                    "id": row.id,
                    "profile_id": row.profile_id,
                    "profile_name": profile_name,
                    "status": row.status,
                    "trigger": row.trigger,
                    "stats": copy.deepcopy(row.stats_json),
                    "started_at": self._as_utc(row.started_at),
                    "finished_at": self._as_utc(row.finished_at),
                }
                for row, profile_name in rows
            ], total

    def get_run_detail(self, run_id: str) -> dict | None:
        with self.database.session() as session:
            result = session.execute(
                select(RunRow, ProfileRow.name)
                .join(ProfileRow, ProfileRow.id == RunRow.profile_id)
                .where(RunRow.id == run_id)
            ).one_or_none()
            if result is None:
                return None
            run, profile_name = result
            source_rows = session.scalars(
                select(RunSourceResultRow)
                .where(RunSourceResultRow.run_id == run_id)
                .order_by(
                    RunSourceResultRow.source_kind,
                    RunSourceResultRow.source_name,
                )
            ).all()
            item_rows = session.execute(
                select(RunItemRow, ContentItemRow)
                .join(ContentItemRow, ContentItemRow.id == RunItemRow.content_item_id)
                .where(RunItemRow.run_id == run_id)
                .order_by(RunItemRow.id)
            ).all()
            items = []
            for item, content in item_rows:
                evaluation_rows = session.execute(
                    select(EvaluationRow, CandidateRow)
                    .join(CandidateRow, CandidateRow.id == EvaluationRow.candidate_id)
                    .where(EvaluationRow.run_item_id == item.id)
                    .order_by(EvaluationRow.accepted.desc(), CandidateRow.btih)
                ).all()
                evaluations = []
                for evaluation, candidate in evaluation_rows:
                    evidence_rows = session.scalars(
                        select(CandidateEvidenceRow)
                        .where(
                            CandidateEvidenceRow.run_item_id == item.id,
                            CandidateEvidenceRow.candidate_id == candidate.id,
                        )
                        .order_by(CandidateEvidenceRow.source)
                    ).all()
                    evidence = [self._evidence_dict(row) for row in evidence_rows]
                    evaluations.append(
                        {
                            "candidate_id": candidate.id,
                            "btih": candidate.btih,
                            "accepted": evaluation.accepted,
                            "reasons": copy.deepcopy(evaluation.reasons_json),
                            "evidence": evidence,
                            "aggregate": self._aggregate_evidence(evidence),
                        }
                    )
                task_rows = session.scalars(
                    select(DownloadTaskRow)
                    .where(DownloadTaskRow.run_item_id == item.id)
                    .order_by(DownloadTaskRow.created_at)
                ).all()
                duplicate = None
                if item.duplicate_task_id:
                    duplicate_result = session.execute(
                        select(DownloadTaskRow, ProfileRow.name)
                        .join(ProfileRow, ProfileRow.id == DownloadTaskRow.profile_id)
                        .where(DownloadTaskRow.id == item.duplicate_task_id)
                    ).one_or_none()
                    if duplicate_result is not None:
                        duplicate_task, duplicate_profile_name = duplicate_result
                        duplicate = {
                            "task_id": duplicate_task.id,
                            "profile_id": duplicate_task.profile_id,
                            "profile_name": duplicate_profile_name,
                            "status": duplicate_task.status,
                            "destination_path": duplicate_task.destination_path,
                        }
                items.append(
                    {
                        "id": item.id,
                        "content_item_id": content.id,
                        "namespace": content.namespace,
                        "raw_key": content.raw_key,
                        "normalized_key": content.normalized_key,
                        "title": content.title,
                        "metadata": copy.deepcopy(content.metadata_json or {}),
                        "result_status": item.result_status,
                        "rankings": copy.deepcopy(item.rankings_json),
                        "evaluations": evaluations,
                        "downloads": [self._download_row_dict(row) for row in task_rows],
                        "duplicate": duplicate,
                    }
                )
            return {
                "id": run.id,
                "profile_id": run.profile_id,
                "profile_name": profile_name,
                "status": run.status,
                "trigger": run.trigger,
                "config_snapshot": copy.deepcopy(run.config_snapshot),
                "stats": copy.deepcopy(run.stats_json),
                "started_at": self._as_utc(run.started_at),
                "finished_at": self._as_utc(run.finished_at),
                "sources": [
                    {
                        "kind": row.source_kind,
                        "name": row.source_name,
                        "status": row.status,
                        "latency_ms": row.latency_ms,
                        "result_count": row.result_count,
                        "error_code": row.error_code,
                    }
                    for row in source_rows
                ],
                "items": items,
            }

    def get_latest_ranking_snapshot(
        self, *, profile_id: int | None = None
    ) -> dict | None:
        conditions = [
            RunRow.status.in_(("success", "partial")),
            RunRow.id.in_(select(RunItemRow.run_id)),
        ]
        if profile_id is not None:
            conditions.append(RunRow.profile_id == profile_id)
        with self.database.session() as session:
            result = session.execute(
                select(RunRow, ProfileRow.name)
                .join(ProfileRow, ProfileRow.id == RunRow.profile_id)
                .where(*conditions)
                .order_by(RunRow.started_at.desc(), RunRow.id.desc())
                .limit(1)
            ).one_or_none()
            if result is None:
                return None
            run, profile_name = result
            rows = session.execute(
                select(RunItemRow, ContentItemRow)
                .join(ContentItemRow, ContentItemRow.id == RunItemRow.content_item_id)
                .where(RunItemRow.run_id == run.id)
            ).all()
            items = [
                {
                    "run_item_id": run_item.id,
                    "namespace": content.namespace,
                    "raw_key": content.raw_key,
                    "normalized_key": content.normalized_key,
                    "title": content.title,
                    "metadata": copy.deepcopy(content.metadata_json or {}),
                    "result_status": run_item.result_status,
                    "rankings": copy.deepcopy(run_item.rankings_json),
                    "best_rank": min(
                        (
                            int(ranking.get("rank", 10**9))
                            for ranking in run_item.rankings_json
                            if isinstance(ranking, dict)
                        ),
                        default=10**9,
                    ),
                }
                for run_item, content in rows
            ]
            items.sort(
                key=lambda item: (
                    item["best_rank"],
                    item["normalized_key"].casefold(),
                )
            )
            return {
                "run_id": run.id,
                "profile_id": run.profile_id,
                "profile_name": profile_name,
                "status": run.status,
                "started_at": self._as_utc(run.started_at),
                "finished_at": self._as_utc(run.finished_at),
                "items": items,
            }

    def get_accepted_run_candidate(
        self, run_id: str, candidate_id: int
    ) -> dict | None:
        with self.database.session() as session:
            result = session.execute(
                select(
                    RunRow,
                    RunItemRow,
                    ContentItemRow,
                    CandidateRow,
                    CandidateEvidenceRow,
                )
                .join(RunItemRow, RunItemRow.run_id == RunRow.id)
                .join(ContentItemRow, ContentItemRow.id == RunItemRow.content_item_id)
                .join(
                    EvaluationRow,
                    (EvaluationRow.run_item_id == RunItemRow.id)
                    & (EvaluationRow.candidate_id == candidate_id),
                )
                .join(CandidateRow, CandidateRow.id == EvaluationRow.candidate_id)
                .join(
                    CandidateEvidenceRow,
                    (CandidateEvidenceRow.run_item_id == RunItemRow.id)
                    & (CandidateEvidenceRow.candidate_id == CandidateRow.id),
                )
                .where(
                    RunRow.id == run_id,
                    RunRow.trigger == "manual_number",
                    EvaluationRow.accepted.is_(True),
                    RunRow.status.in_(("success", "partial")),
                )
                .order_by(CandidateEvidenceRow.id)
                .limit(1)
            ).one_or_none()
            if result is None:
                return None
            run, run_item, content, candidate, evidence = result
            return {
                "run_id": run.id,
                "profile_id": run.profile_id,
                "run_item_id": run_item.id,
                "content_item_id": content.id,
                "candidate_id": candidate.id,
                "btih": candidate.btih,
                "magnet_uri": evidence.magnet_uri,
                "title": content.title,
            }

    def fail_interrupted_runs(self) -> int:
        now = datetime.now(UTC)
        with self.database.session() as session:
            rows = session.scalars(
                select(RunRow).where(RunRow.status == "running")
            ).all()
            for row in rows:
                row.status = "failed"
                row.stats_json = {"error_code": "process_interrupted"}
                row.finished_at = now
            return len(rows)

    def get_run_snapshot(self, run_id: str) -> dict:
        with self.database.session() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            return copy.deepcopy(row.config_snapshot)

    def upsert_content(
        self,
        namespace: str,
        raw_key: str,
        normalized_key: str,
        title: str = "",
        *,
        metadata: dict | None = None,
    ) -> int:
        normalized_namespace = namespace.strip().casefold()
        normalized_identity = normalized_key.strip().casefold()
        incoming_metadata = copy.deepcopy(metadata or {})
        try:
            with self.database.session() as session:
                row = session.scalar(
                    select(ContentItemRow).where(
                        ContentItemRow.namespace == normalized_namespace,
                        ContentItemRow.normalized_key == normalized_identity,
                    )
                )
                if row is not None:
                    if (len(title), title.casefold(), title) > (
                        len(row.title),
                        row.title.casefold(),
                        row.title,
                    ):
                        row.title = title
                    row.metadata_json = {
                        **copy.deepcopy(row.metadata_json or {}),
                        **incoming_metadata,
                    }
                    return row.id
                row = ContentItemRow(
                    namespace=normalized_namespace,
                    raw_key=raw_key,
                    normalized_key=normalized_identity,
                    title=title,
                    metadata_json=incoming_metadata,
                )
                session.add(row)
                session.flush()
                return row.id
        except IntegrityError:
            with self.database.session() as session:
                existing_id = session.scalar(
                    select(ContentItemRow.id).where(
                        ContentItemRow.namespace == normalized_namespace,
                        ContentItemRow.normalized_key == normalized_identity,
                    )
                )
                if existing_id is None:
                    raise
                return existing_id

    def add_run_item(
        self,
        run_id: str,
        content_item_id: int,
        result_status: str,
        *,
        rankings: list[dict],
    ) -> int:
        with self.database.session() as session:
            row = RunItemRow(
                run_id=run_id,
                content_item_id=content_item_id,
                result_status=result_status,
                rankings_json=copy.deepcopy(rankings),
            )
            session.add(row)
            session.flush()
            return row.id

    def update_run_item_status(
        self,
        run_item_id: int,
        status: str,
        *,
        duplicate_task_id: str | None = None,
    ) -> None:
        with self.database.session() as session:
            row = session.get(RunItemRow, run_item_id)
            if row is None:
                raise KeyError(run_item_id)
            row.result_status = status
            if duplicate_task_id is not None:
                row.duplicate_task_id = duplicate_task_id

    def count_run_items(self, run_id: str) -> int:
        with self.database.session() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(RunItemRow)
                    .where(RunItemRow.run_id == run_id)
                )
                or 0
            )

    def count_evaluations(self, run_id: str) -> int:
        with self.database.session() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(EvaluationRow)
                    .join(RunItemRow, RunItemRow.id == EvaluationRow.run_item_id)
                    .where(RunItemRow.run_id == run_id)
                )
                or 0
            )

    def count_candidate_evidence(self, run_id: str) -> int:
        with self.database.session() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(CandidateEvidenceRow)
                    .join(RunItemRow, RunItemRow.id == CandidateEvidenceRow.run_item_id)
                    .where(RunItemRow.run_id == run_id)
                )
                or 0
            )

    def record_run_source_result(
        self,
        run_id: str,
        source_kind: str,
        source_name: str,
        status: str,
        *,
        latency_ms: int | None,
        result_count: int | None,
        error_code: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.add(
                RunSourceResultRow(
                    run_id=run_id,
                    source_kind=source_kind,
                    source_name=source_name,
                    status=status,
                    latency_ms=latency_ms,
                    result_count=result_count,
                    error_code=error_code,
                    error_summary=error_code,
                )
            )

    def list_run_source_results(self, run_id: str) -> list[RunSourceResultRecord]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RunSourceResultRow)
                .where(RunSourceResultRow.run_id == run_id)
                .order_by(
                    RunSourceResultRow.source_kind,
                    RunSourceResultRow.source_name,
                )
            ).all()
            return [
                RunSourceResultRecord(
                    source_kind=row.source_kind,
                    source_name=row.source_name,
                    status=row.status,
                    latency_ms=row.latency_ms,
                    result_count=row.result_count,
                    error_code=row.error_code,
                )
                for row in rows
            ]

    def upsert_candidate(self, btih: str) -> int:
        normalized_btih = btih.strip().casefold()
        try:
            with self.database.session() as session:
                row = session.scalar(
                    select(CandidateRow).where(CandidateRow.btih == normalized_btih)
                )
                if row is not None:
                    return row.id
                row = CandidateRow(btih=normalized_btih)
                session.add(row)
                session.flush()
                return row.id
        except IntegrityError:
            with self.database.session() as session:
                existing_id = session.scalar(
                    select(CandidateRow.id).where(
                        CandidateRow.btih == normalized_btih
                    )
                )
                if existing_id is None:
                    raise
                return existing_id

    def add_candidate_evidence(
        self,
        run_item_id: int,
        candidate_id: int,
        evidence: CandidateEvidence,
    ) -> int:
        with self.database.session() as session:
            row = CandidateEvidenceRow(
                run_item_id=run_item_id,
                candidate_id=candidate_id,
                source=evidence.source,
                magnet_uri=evidence.magnet_uri,
                title=evidence.title,
                size_mb=evidence.size_mb,
                seeders=evidence.seeders,
                chinese_subtitles=evidence.chinese_subtitles.value,
                uncensored=evidence.uncensored.value,
                uhd=evidence.uhd.value,
                notes_json=list(evidence.notes),
            )
            session.add(row)
            session.flush()
            return row.id

    def get_candidate_evidence(self, evidence_id: int) -> CandidateEvidence:
        with self.database.session() as session:
            row = session.get(CandidateEvidenceRow, evidence_id)
            if row is None:
                raise KeyError(evidence_id)
            return CandidateEvidence(
                source=row.source,
                magnet_uri=row.magnet_uri,
                title=row.title,
                size_mb=row.size_mb,
                seeders=row.seeders,
                chinese_subtitles=TruthValue(row.chinese_subtitles),
                uncensored=TruthValue(row.uncensored),
                uhd=TruthValue(row.uhd),
                notes=tuple(row.notes_json),
            )

    def add_evaluation(
        self,
        run_item_id: int,
        candidate_id: int,
        *,
        accepted: bool,
        reasons: list[RuleReason],
    ) -> int:
        with self.database.session() as session:
            row = EvaluationRow(
                run_item_id=run_item_id,
                candidate_id=candidate_id,
                accepted=accepted,
                reasons_json=[{"code": reason.code, "message": reason.message} for reason in reasons],
            )
            session.add(row)
            session.flush()
            return row.id

    def get_evaluation_reasons(self, evaluation_id: int) -> list[RuleReason]:
        with self.database.session() as session:
            row = session.get(EvaluationRow, evaluation_id)
            if row is None:
                raise KeyError(evaluation_id)
            return [RuleReason(value["code"], value["message"]) for value in row.reasons_json]

    def claim_download(
        self,
        download_client_key: str,
        profile_id: int,
        content_item_id: int,
        candidate_id: int,
        *,
        run_item_id: int | None = None,
        generation: int = 0,
        initial_status: str | None = "submitting",
        supersedes_task_id: str | None = None,
        destination_path: str | None = None,
        attention_after_at: datetime | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        try:
            with self.database.session() as session:
                existing_task_id = session.scalar(
                    select(DownloadTaskRow.id).where(
                        DownloadTaskRow.download_client_key == download_client_key,
                        DownloadTaskRow.idempotency_generation == generation,
                        (
                            (DownloadTaskRow.content_item_id == content_item_id)
                            | (DownloadTaskRow.candidate_id == candidate_id)
                        ),
                    )
                )
                if existing_task_id is not None:
                    raise IdempotencyConflict(
                        "content or BTIH is already claimed"
                    )
                task = DownloadTaskRow(
                    id=task_id,
                    download_client_key=download_client_key,
                    profile_id=profile_id,
                    run_item_id=run_item_id,
                    supersedes_task_id=supersedes_task_id,
                    content_item_id=content_item_id,
                    candidate_id=candidate_id,
                    idempotency_generation=generation,
                    status=initial_status,
                    destination_path=destination_path,
                    attention_after_at=attention_after_at,
                    updated_at=now,
                )
                session.add(task)
                session.flush()
                session.add(
                    DownloadEventRow(
                        download_task_id=task_id,
                        sequence=1,
                        status=initial_status,
                        source="system",
                        evidence_json={"kind": "claim_created"},
                        occurred_at=now,
                    )
                )
                session.flush()
        except IntegrityError as exc:
            error_text = str(exc).casefold()
            if (
                "download_tasks.download_client_key, download_tasks.destination_path"
                in error_text
            ):
                raise DestinationBusy(
                    "destination is waiting for task-specific file ownership"
                ) from exc
            if "unique constraint failed: download_tasks" in error_text:
                raise IdempotencyConflict("content or BTIH is already claimed") from exc
            raise
        return task_id

    def append_download_event(
        self,
        task_id: str,
        status: str,
        evidence: dict,
        *,
        source: str = "system",
        occurred_at: datetime | None = None,
        external_reference: str | None = None,
        baseline=None,
        completion_state=None,
        error_code: str | None = None,
        error_summary: str | None = None,
        ownership_acquired: bool | None = None,
    ) -> None:
        with self.database.session() as session:
            task = session.get(DownloadTaskRow, task_id)
            if task is None:
                raise KeyError(task_id)
            sequence = session.scalar(
                select(func.max(DownloadEventRow.sequence)).where(
                    DownloadEventRow.download_task_id == task_id
                )
            )
            event_time = occurred_at or datetime.now(UTC)
            session.add(
                DownloadEventRow(
                    download_task_id=task_id,
                    sequence=(sequence or 0) + 1,
                    status=status,
                    source=source,
                    evidence_json=copy.deepcopy(evidence),
                    occurred_at=event_time,
                )
            )
            task.status = status
            task.updated_at = event_time
            if external_reference is not None:
                task.external_reference = external_reference
            if baseline is not None:
                task.baseline_json = self._serialize_snapshot(baseline)
            if completion_state is not None:
                task.completion_state_json = self._serialize_completion_state(
                    completion_state
                )
            task.last_error_code = error_code
            task.last_error_summary = error_summary
            if ownership_acquired is not None:
                task.ownership_acquired = ownership_acquired
            if source == "manual" and status == "completed":
                task.manual_completed = True

    def list_download_events(self, task_id: str) -> list[DownloadEventRecord]:
        with self.database.session() as session:
            rows = session.scalars(
                select(DownloadEventRow)
                .where(DownloadEventRow.download_task_id == task_id)
                .order_by(DownloadEventRow.sequence)
            ).all()
            return [
                DownloadEventRecord(
                    sequence=row.sequence,
                    status=row.status,
                    source=row.source,
                    evidence=copy.deepcopy(row.evidence_json),
                    occurred_at=row.occurred_at,
                )
                for row in rows
            ]

    def get_latest_download_event(self, task_id: str) -> DownloadEventRecord | None:
        with self.database.session() as session:
            row = session.scalar(
                select(DownloadEventRow)
                .where(DownloadEventRow.download_task_id == task_id)
                .order_by(DownloadEventRow.sequence.desc())
                .limit(1)
            )
            if row is None:
                return None
            return DownloadEventRecord(
                sequence=row.sequence,
                status=row.status,
                source=row.source,
                evidence=copy.deepcopy(row.evidence_json),
                occurred_at=row.occurred_at,
            )

    def count_download_tasks(self) -> int:
        with self.database.session() as session:
            return session.scalar(select(func.count()).select_from(DownloadTaskRow)) or 0

    def list_unfinished_download_task_ids(self) -> list[str]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(DownloadTaskRow.id)
                    .where(
                        DownloadTaskRow.status.in_(
                            (
                                "submitting",
                                "submitted",
                                "downloading",
                                "attention_required",
                            )
                        )
                    )
                    .order_by(DownloadTaskRow.updated_at, DownloadTaskRow.id)
                ).all()
            )

    def get_download_task(self, task_id: str) -> DownloadTaskRecord | None:
        with self.database.session() as session:
            result = session.execute(
                select(DownloadTaskRow, CandidateRow.btih)
                .join(CandidateRow, CandidateRow.id == DownloadTaskRow.candidate_id)
                .where(DownloadTaskRow.id == task_id)
            ).one_or_none()
            if result is None:
                return None
            row, btih = result
            return DownloadTaskRecord(
                id=row.id,
                status=row.status,
                download_client_key=row.download_client_key,
                profile_id=row.profile_id,
                run_item_id=row.run_item_id,
                content_item_id=row.content_item_id,
                candidate_id=row.candidate_id,
                btih=btih,
                generation=row.idempotency_generation,
                supersedes_task_id=row.supersedes_task_id,
                destination_path=row.destination_path,
                baseline=self._deserialize_snapshot(row.baseline_json),
                completion_state=self._deserialize_completion_state(
                    row.completion_state_json
                ),
                attention_after_at=self._as_utc(row.attention_after_at),
                external_reference=row.external_reference,
                last_error_code=row.last_error_code,
                last_error_summary=row.last_error_summary,
                ownership_acquired=row.ownership_acquired,
                manual_completed=row.manual_completed,
            )

    def list_downloads(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        conditions = [] if status is None else [DownloadTaskRow.status == status]
        with self.database.session() as session:
            total = session.scalar(
                select(func.count()).select_from(DownloadTaskRow).where(*conditions)
            ) or 0
            rows = session.execute(
                select(
                    DownloadTaskRow,
                    CandidateRow.btih,
                    ContentItemRow,
                    ProfileRow.name,
                )
                .join(CandidateRow, CandidateRow.id == DownloadTaskRow.candidate_id)
                .join(ContentItemRow, ContentItemRow.id == DownloadTaskRow.content_item_id)
                .join(ProfileRow, ProfileRow.id == DownloadTaskRow.profile_id)
                .where(*conditions)
                .order_by(DownloadTaskRow.updated_at.desc(), DownloadTaskRow.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [
                self._download_joined_dict(row, btih, content, profile_name)
                for row, btih, content, profile_name in rows
            ], total

    def get_download_detail(self, task_id: str) -> dict | None:
        with self.database.session() as session:
            result = session.execute(
                select(
                    DownloadTaskRow,
                    CandidateRow.btih,
                    ContentItemRow,
                    ProfileRow.name,
                )
                .join(CandidateRow, CandidateRow.id == DownloadTaskRow.candidate_id)
                .join(ContentItemRow, ContentItemRow.id == DownloadTaskRow.content_item_id)
                .join(ProfileRow, ProfileRow.id == DownloadTaskRow.profile_id)
                .where(DownloadTaskRow.id == task_id)
            ).one_or_none()
            if result is None:
                return None
            row, btih, content, profile_name = result
            detail = self._download_joined_dict(row, btih, content, profile_name)
            events = session.scalars(
                select(DownloadEventRow)
                .where(DownloadEventRow.download_task_id == task_id)
                .order_by(DownloadEventRow.sequence)
            ).all()
            detail["events"] = [
                {
                    "sequence": event.sequence,
                    "status": event.status,
                    "source": event.source,
                    "evidence": copy.deepcopy(event.evidence_json),
                    "occurred_at": self._as_utc(event.occurred_at),
                }
                for event in events
            ]
            return detail

    def find_download_by_btih(self, btih: str) -> dict | None:
        with self.database.session() as session:
            result = session.execute(
                select(DownloadTaskRow, ProfileRow.name)
                .join(CandidateRow, CandidateRow.id == DownloadTaskRow.candidate_id)
                .join(ProfileRow, ProfileRow.id == DownloadTaskRow.profile_id)
                .where(CandidateRow.btih == btih.casefold())
                .order_by(DownloadTaskRow.created_at.desc())
                .limit(1)
            ).one_or_none()
            if result is None:
                return None
            row, profile_name = result
            return {
                "task_id": row.id,
                "profile_id": row.profile_id,
                "profile_name": profile_name,
                "status": row.status,
                "generation": row.idempotency_generation,
            }

    def find_download_conflict(
        self,
        download_client_key: str,
        content_item_id: int,
        candidate_id: int,
        *,
        generation: int = 0,
    ) -> dict | None:
        with self.database.session() as session:
            result = session.execute(
                select(DownloadTaskRow, ProfileRow.name, CandidateRow.btih)
                .join(ProfileRow, ProfileRow.id == DownloadTaskRow.profile_id)
                .join(CandidateRow, CandidateRow.id == DownloadTaskRow.candidate_id)
                .where(
                    DownloadTaskRow.download_client_key == download_client_key,
                    DownloadTaskRow.idempotency_generation == generation,
                    (
                        (DownloadTaskRow.content_item_id == content_item_id)
                        | (DownloadTaskRow.candidate_id == candidate_id)
                    ),
                )
                .order_by(DownloadTaskRow.created_at, DownloadTaskRow.id)
                .limit(1)
            ).one_or_none()
            if result is None:
                return None
            row, profile_name, btih = result
            return {
                "task_id": row.id,
                "profile_id": row.profile_id,
                "profile_name": profile_name,
                "status": row.status,
                "btih": btih,
                "destination_path": row.destination_path,
            }

    def list_source_health(self) -> list[SourceHealthRecord]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SourceHealthRow).order_by(
                    SourceHealthRow.source_kind,
                    SourceHealthRow.source_name,
                )
            ).all()
            return [self._source_health_record(row) for row in rows]

    def download_export_rows(self) -> list[dict]:
        with self.database.session() as session:
            joined = session.execute(
                select(
                    DownloadTaskRow,
                    CandidateRow.btih,
                    ContentItemRow,
                    ProfileRow.name,
                )
                .join(CandidateRow, CandidateRow.id == DownloadTaskRow.candidate_id)
                .join(ContentItemRow, ContentItemRow.id == DownloadTaskRow.content_item_id)
                .join(ProfileRow, ProfileRow.id == DownloadTaskRow.profile_id)
                .order_by(DownloadTaskRow.created_at.desc(), DownloadTaskRow.id.desc())
            ).all()
            rows = []
            for task, btih, content, profile_name in joined:
                result = self._download_joined_dict(
                    task, btih, content, profile_name
                )
                result.update(
                    {
                        "ranking_sources": "manual",
                        "rank": "",
                        "candidate_sources": "manual",
                        "evaluation": "manual",
                    }
                )
                if task.run_item_id is not None:
                    run_item = session.get(RunItemRow, task.run_item_id)
                    if run_item is not None:
                        rankings = run_item.rankings_json or []
                        result["ranking_sources"] = ",".join(
                            sorted(
                                {
                                    str(value.get("source", ""))
                                    for value in rankings
                                    if value.get("source")
                                }
                            )
                        )
                        rank_values = [
                            value.get("rank")
                            for value in rankings
                            if isinstance(value.get("rank"), int)
                        ]
                        result["rank"] = min(rank_values) if rank_values else ""
                        evidence_sources = session.scalars(
                            select(CandidateEvidenceRow.source).where(
                                CandidateEvidenceRow.run_item_id == task.run_item_id,
                                CandidateEvidenceRow.candidate_id == task.candidate_id,
                            )
                        ).all()
                        result["candidate_sources"] = ",".join(
                            sorted(set(evidence_sources))
                        )
                        evaluation = session.scalar(
                            select(EvaluationRow.accepted).where(
                                EvaluationRow.run_item_id == task.run_item_id,
                                EvaluationRow.candidate_id == task.candidate_id,
                            )
                        )
                        result["evaluation"] = (
                            "accepted" if evaluation is True else "rejected"
                        )
                rows.append(result)
            return rows

    def dashboard_summary(self) -> dict:
        with self.database.session() as session:
            status_counts = dict(
                session.execute(
                    select(DownloadTaskRow.status, func.count())
                    .group_by(DownloadTaskRow.status)
                ).all()
            )
            return {
                "active_profiles": session.scalar(
                    select(func.count()).select_from(ProfileRow).where(
                        ProfileRow.enabled.is_(True),
                        ProfileRow.archived_at.is_(None),
                    )
                ) or 0,
                "running_runs": session.scalar(
                    select(func.count()).select_from(RunRow).where(RunRow.status == "running")
                ) or 0,
                "download_statuses": status_counts,
                "unhealthy_sources": session.scalar(
                    select(func.count()).select_from(SourceHealthRow).where(
                        SourceHealthRow.consecutive_failures > 0
                    )
                ) or 0,
            }

    def get_candidate_btih(self, candidate_id: int) -> str:
        with self.database.session() as session:
            row = session.get(CandidateRow, candidate_id)
            if row is None:
                raise KeyError(candidate_id)
            return row.btih

    def next_download_generation(
        self, download_client_key: str, content_item_id: int, candidate_id: int
    ) -> int:
        with self.database.session() as session:
            current = session.scalar(
                select(func.max(DownloadTaskRow.idempotency_generation)).where(
                    DownloadTaskRow.download_client_key == download_client_key,
                    (
                        (DownloadTaskRow.content_item_id == content_item_id)
                        | (DownloadTaskRow.candidate_id == candidate_id)
                    ),
                )
            )
            return (current or 0) + 1

    def add_audit_event(
        self,
        event_type: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: dict | None = None,
        occurred_at: datetime | None = None,
    ) -> int:
        with self.database.session() as session:
            row = AuditEventRow(
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                details_json=copy.deepcopy(details or {}),
                occurred_at=occurred_at or datetime.now(UTC),
            )
            session.add(row)
            session.flush()
            return row.id

    def count_audit_events(self, event_type: str) -> int:
        with self.database.session() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEventRow)
                    .where(AuditEventRow.event_type == event_type)
                )
                or 0
            )

    @staticmethod
    def _serialize_snapshot(snapshot) -> dict:
        return {
            "files": [
                {"path": file.path, "size": file.size} for file in snapshot.files
            ]
        }

    @staticmethod
    def _deserialize_snapshot(value: dict | None):
        if value is None:
            return None
        from tuntu.downloaders.clouddrive import DirectorySnapshot, FileFact

        return DirectorySnapshot(
            tuple(FileFact(item["path"], item["size"]) for item in value["files"])
        )

    @classmethod
    def _serialize_completion_state(cls, state) -> dict:
        return {
            "baseline": cls._serialize_snapshot(state.baseline),
            "destination_path": state.destination_path,
            "required_stable_observations": state.required_stable_observations,
            "owned_paths": list(state.owned_paths),
            "last_fingerprint": [list(item) for item in state.last_fingerprint],
            "stable_observations": state.stable_observations,
        }

    @classmethod
    def _deserialize_completion_state(cls, value: dict | None):
        if value is None:
            return None
        from tuntu.downloads.completion import CompletionState

        return CompletionState(
            baseline=cls._deserialize_snapshot(value["baseline"]),
            destination_path=value.get("destination_path", "/"),
            required_stable_observations=value["required_stable_observations"],
            owned_paths=tuple(value["owned_paths"]),
            last_fingerprint=tuple(
                (item[0], item[1]) for item in value["last_fingerprint"]
            ),
            stable_observations=value["stable_observations"],
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @classmethod
    def _profile_record(cls, row: ProfileRow) -> ProfileRecord:
        return ProfileRecord(
            id=row.id,
            name=row.name,
            settings=copy.deepcopy(row.settings_json),
            destination_subdir=row.destination_subdir,
            top_n=row.top_n,
            daily_time=row.daily_time,
            enabled=row.enabled,
            archived_at=cls._as_utc(row.archived_at),
        )

    @classmethod
    def _run_record(cls, row: RunRow) -> RunRecord:
        return RunRecord(
            id=row.id,
            profile_id=row.profile_id,
            status=row.status,
            trigger=row.trigger,
            config_snapshot=copy.deepcopy(row.config_snapshot),
            stats=copy.deepcopy(row.stats_json),
            started_at=cls._as_utc(row.started_at),
            finished_at=cls._as_utc(row.finished_at),
        )

    def record_source_success(
        self,
        kind: str,
        name: str,
        latency_ms: int,
        result_count: int,
        checked_at: datetime | None = None,
    ) -> None:
        observed_at = checked_at or datetime.now(UTC)
        with self.database.session() as session:
            session.execute(
                sqlite_insert(SourceHealthRow)
                .values(
                    source_kind=kind,
                    source_name=name,
                    last_success_at=observed_at,
                    last_checked_at=observed_at,
                    last_latency_ms=latency_ms,
                    last_result_count=result_count,
                    consecutive_failures=0,
                    last_error_code=None,
                    last_error_summary=None,
                )
                .on_conflict_do_update(
                    index_elements=["source_kind", "source_name"],
                    set_={
                        "last_success_at": observed_at,
                        "last_checked_at": observed_at,
                        "last_latency_ms": latency_ms,
                        "last_result_count": result_count,
                        "consecutive_failures": 0,
                        "last_error_code": None,
                        "last_error_summary": None,
                    },
                )
            )

    def record_source_failure(
        self,
        kind: str,
        name: str,
        error_code: str,
        error_summary: str,
        latency_ms: int,
        checked_at: datetime | None = None,
    ) -> None:
        observed_at = checked_at or datetime.now(UTC)
        with self.database.session() as session:
            session.execute(
                sqlite_insert(SourceHealthRow)
                .values(
                    source_kind=kind,
                    source_name=name,
                    last_checked_at=observed_at,
                    last_latency_ms=latency_ms,
                    consecutive_failures=1,
                    last_error_code=error_code,
                    last_error_summary=error_summary,
                )
                .on_conflict_do_update(
                    index_elements=["source_kind", "source_name"],
                    set_={
                        "last_checked_at": observed_at,
                        "last_latency_ms": latency_ms,
                        "consecutive_failures": (
                            SourceHealthRow.consecutive_failures + 1
                        ),
                        "last_error_code": error_code,
                        "last_error_summary": error_summary,
                    },
                )
            )

    def get_source_health(self, kind: str, name: str) -> SourceHealthRecord | None:
        with self.database.session() as session:
            row = session.get(SourceHealthRow, (kind, name))
            if row is None:
                return None
            return SourceHealthRecord(
                source_kind=row.source_kind,
                source_name=row.source_name,
                last_success_at=row.last_success_at,
                last_checked_at=row.last_checked_at,
                last_latency_ms=row.last_latency_ms,
                last_result_count=row.last_result_count,
                consecutive_failures=row.consecutive_failures,
                last_error_code=row.last_error_code,
                last_error_summary=row.last_error_summary,
            )

    @classmethod
    def _source_health_record(cls, row: SourceHealthRow) -> SourceHealthRecord:
        return SourceHealthRecord(
            source_kind=row.source_kind,
            source_name=row.source_name,
            last_success_at=cls._as_utc(row.last_success_at),
            last_checked_at=cls._as_utc(row.last_checked_at),
            last_latency_ms=row.last_latency_ms,
            last_result_count=row.last_result_count,
            consecutive_failures=row.consecutive_failures,
            last_error_code=row.last_error_code,
            last_error_summary=row.last_error_summary,
        )

    @classmethod
    def _evidence_dict(cls, row: CandidateEvidenceRow) -> dict:
        return {
            "source": row.source,
            "magnet_uri": row.magnet_uri,
            "title": row.title,
            "size_mb": row.size_mb,
            "seeders": row.seeders,
            "chinese_subtitles": row.chinese_subtitles,
            "uncensored": row.uncensored,
            "uhd": row.uhd,
            "notes": copy.deepcopy(row.notes_json),
        }

    @staticmethod
    def _aggregate_evidence(evidence: list[dict]) -> dict:
        sizes = {
            value["size_mb"]
            for value in evidence
            if value["size_mb"] is not None
        }
        truths = {}
        for field in ("chinese_subtitles", "uncensored", "uhd"):
            known = {
                value[field]
                for value in evidence
                if value[field] != "unknown"
            }
            truths[field] = next(iter(known)) if len(known) == 1 else "unknown"
        return {
            "title": max(
                (value["title"] for value in evidence),
                key=lambda value: (len(value), value.casefold(), value),
                default="",
            ),
            "size_mb": next(iter(sizes)) if len(sizes) == 1 else None,
            "seeders": max(
                (
                    value["seeders"]
                    for value in evidence
                    if value["seeders"] is not None
                ),
                default=None,
            ),
            **truths,
            "sources": sorted({value["source"] for value in evidence}),
        }

    @classmethod
    def _download_row_dict(cls, row: DownloadTaskRow) -> dict:
        return {
            "id": row.id,
            "status": row.status,
            "profile_id": row.profile_id,
            "run_item_id": row.run_item_id,
            "content_item_id": row.content_item_id,
            "candidate_id": row.candidate_id,
            "generation": row.idempotency_generation,
            "supersedes_task_id": row.supersedes_task_id,
            "destination_path": row.destination_path,
            "last_error_code": row.last_error_code,
            "manual_completed": row.manual_completed,
            "created_at": cls._as_utc(row.created_at),
            "updated_at": cls._as_utc(row.updated_at),
        }

    @classmethod
    def _download_joined_dict(cls, row, btih, content, profile_name) -> dict:
        result = cls._download_row_dict(row)
        result.update(
            {
                "btih": btih,
                "profile_name": profile_name,
                "content_namespace": content.namespace,
                "content_key": content.normalized_key,
                "title": content.title,
            }
        )
        return result
