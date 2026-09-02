from __future__ import annotations

import copy
import threading
from dataclasses import dataclass

from tuntu.db import (
    DestinationBusy,
    IdempotencyConflict,
    Repository,
    RunAlreadyActive,
)
from tuntu.downloaders.clouddrive import (
    resolve_destination,
    resolve_task_destination,
)
from tuntu.downloads.state import DownloadStatus
from tuntu.models import ContentItem, ContentResultStatus
from tuntu.providers.manual import ManualDiscoveryProvider
from tuntu.rules import RuleMode, RuleSet
from tuntu.selector import evaluation_sort_key


@dataclass(frozen=True, slots=True)
class RunExecution:
    run_id: str | None
    status: str
    skipped_reason: str | None = None


@dataclass(slots=True)
class _SourceAggregate:
    kind: str
    name: str
    latency_ms: int = 0
    result_count: int = 0
    successes: int = 0
    failures: int = 0
    error_code: str | None = None

    def add(self, outcome) -> None:
        self.latency_ms += outcome.latency_ms
        self.result_count += outcome.result_count
        if outcome.status == "success":
            self.successes += 1
        else:
            self.failures += 1
            self.error_code = outcome.error_code

    @property
    def status(self) -> str:
        if self.failures and self.successes:
            return "partial"
        return "failed" if self.failures else "success"


class RunService:
    def __init__(
        self,
        *,
        repository: Repository,
        provider_runner,
        discovery_providers,
        candidate_providers,
        download_service,
        max_concurrent_runs: int = 2,
    ):
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be positive")
        self.repository = repository
        self.provider_runner = provider_runner
        self.discovery_providers = {
            provider.name: provider for provider in discovery_providers
        }
        self.candidate_providers = {
            provider.name: provider for provider in candidate_providers
        }
        self.download_service = download_service
        self._global_slots = threading.BoundedSemaphore(max_concurrent_runs)
        self._locks_guard = threading.Lock()
        self._profile_locks: dict[int, threading.Lock] = {}

    def execute(
        self,
        profile_id: int,
        *,
        trigger: str,
        force_dry_run: bool = False,
        manual_raw_keys: list[str] | None = None,
        auto_submit_override: bool | None = None,
    ) -> RunExecution:
        key_trigger = trigger in {"manual_number", "watchlist"}
        if (manual_raw_keys is not None) != key_trigger:
            raise ValueError(
                "manual content keys require the manual_number or watchlist trigger"
            )
        if trigger == "watchlist":
            if not isinstance(auto_submit_override, bool):
                raise ValueError("watchlist trigger requires an auto-submit override")
        elif auto_submit_override is not None:
            raise ValueError("auto-submit override is only valid for watchlist runs")
        profile_lock = self._profile_lock(profile_id)
        if not profile_lock.acquire(blocking=False):
            self._record_skip(profile_id, "profile_overlap")
            return RunExecution(None, "skipped", "profile_overlap")
        if not self._global_slots.acquire(blocking=False):
            profile_lock.release()
            self._record_skip(profile_id, "global_concurrency_limit")
            return RunExecution(None, "skipped", "global_concurrency_limit")
        try:
            return self._execute_locked(
                profile_id,
                trigger=trigger,
                force_dry_run=force_dry_run,
                manual_raw_keys=manual_raw_keys,
                auto_submit_override=auto_submit_override,
            )
        finally:
            self._global_slots.release()
            profile_lock.release()

    def _execute_locked(
        self,
        profile_id: int,
        *,
        trigger: str,
        force_dry_run: bool,
        manual_raw_keys: list[str] | None,
        auto_submit_override: bool | None,
    ) -> RunExecution:
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        if profile.archived_at is not None:
            raise ValueError("archived profile cannot run")
        if trigger == "scheduled" and not profile.enabled:
            self._record_skip(profile_id, "profile_disabled")
            return RunExecution(None, "skipped", "profile_disabled")

        snapshot = self._snapshot(profile, force_dry_run=force_dry_run)
        if manual_raw_keys is not None:
            if not manual_raw_keys:
                raise ValueError("manual run requires at least one content key")
            snapshot.update(
                {
                    "scope": "manual" if trigger == "manual_number" else "watchlist",
                    "discovery_sources": ["manual"],
                    "manual_raw_keys": list(manual_raw_keys),
                    "top_n": min(len(manual_raw_keys), 100),
                }
            )
            if trigger == "manual_number":
                snapshot.update(
                    {
                        "configured_auto_submit": False,
                        "effective_auto_submit": False,
                    }
                )
            else:
                snapshot.update(
                    {
                        "configured_auto_submit": auto_submit_override,
                        "effective_auto_submit": bool(auto_submit_override)
                        and not force_dry_run,
                    }
                )
        try:
            run_id = self.repository.create_run(
                profile_id, snapshot, trigger=trigger
            )
        except RunAlreadyActive:
            self._record_skip(profile_id, "database_active_run")
            return RunExecution(None, "skipped", "database_active_run")

        stats = self._empty_stats()
        source_results: dict[tuple[str, str], _SourceAggregate] = {}
        terminal_status = "failed"
        try:
            discoveries, candidates, rules = self._initialize(snapshot)
            discovery_batch = self.provider_runner.collect(
                discoveries, snapshot["scope"], run_id=run_id
            )
            self._aggregate(source_results, discovery_batch.outcomes)
            stats["source_failures"] += len(discovery_batch.failures)
            if not any(
                outcome.status == "success" for outcome in discovery_batch.outcomes
            ):
                stats["error_code"] = "all_discovery_sources_failed"
                terminal_status = "failed"
                return RunExecution(run_id, terminal_status)

            items = self._merge_items(discovery_batch.values)[: snapshot["top_n"]]
            stats["items_discovered"] = len(items)
            had_partial_failure = bool(discovery_batch.failures)
            for content in items:
                try:
                    item_partial = self._process_item(
                        run_id,
                        profile_id,
                        content,
                        candidates,
                        rules,
                        snapshot,
                        stats,
                        source_results,
                    )
                except Exception:
                    stats["item_failures"] += 1
                    had_partial_failure = True
                    continue
                had_partial_failure = had_partial_failure or item_partial
                stats["items_processed"] += 1

            terminal_status = "partial" if had_partial_failure else "success"
            return RunExecution(run_id, terminal_status)
        except Exception:
            stats["error_code"] = "run_initialization_or_execution_failed"
            terminal_status = "failed"
            return RunExecution(run_id, terminal_status)
        finally:
            for aggregate in source_results.values():
                self.repository.record_run_source_result(
                    run_id,
                    aggregate.kind,
                    aggregate.name,
                    aggregate.status,
                    latency_ms=aggregate.latency_ms,
                    result_count=aggregate.result_count,
                    error_code=aggregate.error_code,
                )
            self.repository.finish_run(run_id, terminal_status, stats)
            self._finish_provider_run(run_id)

    def _process_item(
        self,
        run_id,
        profile_id,
        content,
        candidate_providers,
        rules,
        snapshot,
        stats,
        source_results,
    ) -> bool:
        batch = self.provider_runner.search(
            candidate_providers, content, run_id=run_id
        )
        self._aggregate(source_results, batch.outcomes)
        stats["source_failures"] += len(batch.failures)
        partial = bool(batch.failures)

        merged_candidates = {}
        for candidate in batch.values:
            if candidate.item_identity != content.identity:
                raise ValueError("candidate belongs to another content item")
            existing = merged_candidates.get(candidate.btih)
            if existing is None:
                merged_candidates[candidate.btih] = candidate
            else:
                existing.merge_from(candidate)
        evaluations = [rules.evaluate(value) for value in merged_candidates.values()]
        evaluations.sort(key=evaluation_sort_key)
        accepted = [evaluation for evaluation in evaluations if evaluation.accepted]

        if not evaluations:
            result_status = ContentResultStatus.NO_CANDIDATE.value
            stats["items_no_candidate"] += 1
        elif not accepted:
            result_status = ContentResultStatus.FILTERED.value
            stats["items_filtered"] += 1
        else:
            result_status = ContentResultStatus.SELECTED.value
            stats["items_selected"] += 1
        content_id = self.repository.upsert_content(
            content.namespace,
            content.raw_key,
            content.normalized_key,
            content.title,
            metadata=content.metadata,
        )
        run_item_id = self.repository.add_run_item(
            run_id,
            content_id,
            result_status,
            rankings=[
                {
                    "source": ranking.source,
                    "rank": ranking.rank,
                    "raw_key": ranking.raw_key,
                    "scope": ranking.scope,
                }
                for ranking in content.rankings
            ],
        )
        candidate_ids = {}
        for evaluation in evaluations:
            candidate_id = self.repository.upsert_candidate(
                evaluation.candidate.btih
            )
            candidate_ids[evaluation.candidate.btih] = candidate_id
            for evidence in evaluation.candidate.evidence:
                self.repository.add_candidate_evidence(
                    run_item_id, candidate_id, evidence
                )
            self.repository.add_evaluation(
                run_item_id,
                candidate_id,
                accepted=evaluation.accepted,
                reasons=evaluation.reasons,
            )
        stats["candidates_found"] += len(evaluations)
        stats["candidates_accepted"] += len(accepted)

        if not snapshot["effective_auto_submit"] or not accepted:
            return partial

        generation = 0
        supersedes_task_id = None
        for index, evaluation in enumerate(accepted):
            stats["submit_attempts"] += 1
            candidate = evaluation.candidate
            try:
                task_id = self.download_service.submit_candidate(
                    profile_id=profile_id,
                    content_item_id=content_id,
                    candidate_id=candidate_ids[candidate.btih],
                    magnet_uri=candidate.magnet_uri,
                    destination=resolve_task_destination(
                        snapshot["destination"], candidate.btih
                    ),
                    run_item_id=run_item_id,
                    generation=generation,
                    supersedes_task_id=supersedes_task_id,
                )
            except IdempotencyConflict:
                stats["items_deduplicated"] += 1
                conflict = self.repository.find_download_conflict(
                    self.download_service.DOWNLOAD_CLIENT_KEY,
                    content_id,
                    candidate_ids[candidate.btih],
                )
                self.repository.update_run_item_status(
                    run_item_id,
                    "deduplicated",
                    duplicate_task_id=(
                        conflict["task_id"] if conflict is not None else None
                    ),
                )
                return partial
            except DestinationBusy:
                stats["submit_failures"] += 1
                self.repository.update_run_item_status(run_item_id, "submit_failed")
                return True

            task = self.repository.get_download_task(task_id)
            if (
                task.status == DownloadStatus.FAILED
                and task.last_error_code == "explicit_rejection"
            ):
                stats["submit_failures"] += 1
                partial = True
                if index + 1 < len(accepted):
                    stats["candidate_fallbacks"] += 1
                    supersedes_task_id = task_id
                    generation = self.repository.next_download_generation(
                        task.download_client_key,
                        content_id,
                        candidate_ids[accepted[index + 1].candidate.btih],
                    )
                    continue
            elif task.status == DownloadStatus.FAILED:
                stats["submit_failures"] += 1
                self.repository.update_run_item_status(run_item_id, "submit_failed")
                return True
            elif task.status == DownloadStatus.SUBMITTING:
                stats["submit_unknown"] += 1
                self.repository.update_run_item_status(run_item_id, "submit_unknown")
                return True
            elif task.status == DownloadStatus.ATTENTION_REQUIRED:
                stats["submit_attention_required"] += 1
                self.repository.update_run_item_status(
                    run_item_id, "attention_required"
                )
                return True
            else:
                stats["items_submitted"] += 1
                self.repository.update_run_item_status(run_item_id, "submitted")
                return partial
            break
        self.repository.update_run_item_status(run_item_id, "submit_failed")
        return True

    def _initialize(self, snapshot):
        if snapshot["discovery_sources"] == ["manual"]:
            discoveries = [
                ManualDiscoveryProvider(list(snapshot.get("manual_raw_keys", [])))
            ]
        else:
            discoveries = self._selected(
                self.discovery_providers, snapshot["discovery_sources"]
            )
        candidates = self._selected(
            self.candidate_providers, snapshot["candidate_sources"]
        )
        if not discoveries or not candidates:
            raise ValueError("profile requires discovery and candidate sources")
        raw_rules = copy.deepcopy(snapshot["rules"])
        for key in ("chinese_subtitles", "uncensored", "uhd"):
            if key in raw_rules:
                raw_rules[key] = RuleMode(raw_rules[key])
        for key in ("include_keywords", "exclude_keywords"):
            if key in raw_rules:
                raw_rules[key] = tuple(raw_rules[key])
        return discoveries, candidates, RuleSet(**raw_rules)

    def _snapshot(self, profile, *, force_dry_run: bool) -> dict:
        settings = copy.deepcopy(profile.settings)
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "scope": settings.get("scope", "weekly"),
            "discovery_sources": list(settings.get("discovery_sources", [])),
            "candidate_sources": list(settings.get("candidate_sources", [])),
            "rules": copy.deepcopy(settings.get("rules", {})),
            "top_n": profile.top_n,
            "destination": resolve_destination(
                self.download_service.client.config.root_path,
                profile.destination_subdir,
            ),
            "configured_auto_submit": bool(settings.get("auto_submit", False)),
            "effective_auto_submit": bool(settings.get("auto_submit", False))
            and not force_dry_run,
        }

    @staticmethod
    def _selected(registry, names):
        return [registry[name] for name in names if name in registry]

    @staticmethod
    def _merge_items(values) -> list[ContentItem]:
        merged = {}
        for content in values:
            existing = merged.get(content.identity)
            if existing is None:
                merged[content.identity] = content
            else:
                existing.merge_from(content)
        return sorted(
            merged.values(), key=lambda content: (content.best_rank, content.identity)
        )

    @staticmethod
    def _aggregate(target, outcomes) -> None:
        for outcome in outcomes:
            key = (outcome.kind, outcome.source)
            aggregate = target.setdefault(
                key, _SourceAggregate(outcome.kind, outcome.source)
            )
            aggregate.add(outcome)

    def _finish_provider_run(self, run_id: str) -> None:
        seen = set()
        for provider in (
            *self.discovery_providers.values(),
            *self.candidate_providers.values(),
        ):
            http = getattr(provider, "http", None)
            if http is not None and id(http) not in seen:
                seen.add(id(http))
                http.finish_run(run_id)

    def _profile_lock(self, profile_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._profile_locks.setdefault(profile_id, threading.Lock())

    def _record_skip(self, profile_id: int, reason: str) -> None:
        self.repository.add_audit_event(
            "run_skipped",
            entity_type="profile",
            entity_id=str(profile_id),
            details={"reason": reason},
        )

    @staticmethod
    def _empty_stats() -> dict:
        return {
            "items_discovered": 0,
            "items_processed": 0,
            "items_no_candidate": 0,
            "items_filtered": 0,
            "items_selected": 0,
            "candidates_found": 0,
            "candidates_accepted": 0,
            "submit_attempts": 0,
            "items_submitted": 0,
            "items_deduplicated": 0,
            "submit_failures": 0,
            "submit_unknown": 0,
            "submit_attention_required": 0,
            "candidate_fallbacks": 0,
            "source_failures": 0,
            "item_failures": 0,
        }
