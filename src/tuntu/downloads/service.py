from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tuntu.db import Repository
from tuntu.downloaders.clouddrive import (
    CloudDriveError,
    CloudDriveRejected,
    ExternalTaskAlreadyExists,
    ResultUnknown,
    TaskSignal,
)
from tuntu.magnet import parse_magnet

from .completion import CompletionState, observe_completion
from .state import DownloadStatus, transition_status


class ConfirmationRequired(ValueError):
    pass


class DownloadService:
    DOWNLOAD_CLIENT_KEY = "clouddrive2"

    def __init__(self, repository: Repository, client, *, now=None):
        self.repository = repository
        self.client = client
        self._now = now or (lambda: datetime.now(UTC))

    def submit_candidate(
        self,
        *,
        profile_id: int,
        content_item_id: int,
        candidate_id: int,
        magnet_uri: str,
        destination: str,
        run_item_id: int | None = None,
        generation: int = 0,
        supersedes_task_id: str | None = None,
    ) -> str:
        parsed = parse_magnet(magnet_uri)
        if self.repository.get_candidate_btih(candidate_id) != parsed.btih:
            raise ValueError("candidate BTIH does not match magnet")
        now = self._now()
        attention_after = now + timedelta(
            seconds=self.client.config.attention_after_seconds
        )
        task_id = self.repository.claim_download(
            self.DOWNLOAD_CLIENT_KEY,
            profile_id,
            content_item_id,
            candidate_id,
            run_item_id=run_item_id,
            generation=generation,
            supersedes_task_id=supersedes_task_id,
            destination_path=destination,
            attention_after_at=attention_after,
        )
        try:
            self.client.ensure_destination(destination)
            result = self.client.submit(parsed.canonical_uri, destination)
        except ResultUnknown as exc:
            baseline = exc.baseline
            completion = (
                CompletionState(
                    baseline,
                    destination_path=destination,
                    required_stable_observations=(
                        self.client.config.required_stable_observations
                    ),
                )
                if baseline is not None
                else None
            )
            self.repository.append_download_event(
                task_id,
                DownloadStatus.SUBMITTING,
                {"kind": "submission_result_unknown"},
                occurred_at=now,
                baseline=baseline,
                completion_state=completion,
                error_code=exc.code,
                error_summary=exc.code,
            )
        except ExternalTaskAlreadyExists as exc:
            self.repository.append_download_event(
                task_id,
                DownloadStatus.ATTENTION_REQUIRED,
                {"kind": "external_task_already_exists"},
                occurred_at=now,
                error_code=exc.code,
                error_summary=exc.code,
            )
        except CloudDriveRejected as exc:
            self.repository.append_download_event(
                task_id,
                DownloadStatus.FAILED,
                {"kind": "explicit_rejection"},
                occurred_at=now,
                error_code=exc.code,
                error_summary=exc.code,
            )
        except CloudDriveError as exc:
            self.repository.append_download_event(
                task_id,
                DownloadStatus.FAILED,
                {"kind": "client_error"},
                occurred_at=now,
                error_code=exc.code,
                error_summary=exc.code,
            )
        else:
            completion = CompletionState(
                result.baseline,
                destination_path=result.destination,
                required_stable_observations=(
                    self.client.config.required_stable_observations
                ),
            )
            self.repository.append_download_event(
                task_id,
                DownloadStatus.SUBMITTED,
                {"kind": "accepted"},
                occurred_at=now,
                baseline=result.baseline,
                completion_state=completion,
                external_reference=result.btih,
            )
        return task_id

    def poll(self, task_id: str) -> DownloadStatus:
        task = self._require_task(task_id)
        current = DownloadStatus(task.status)
        if current in {DownloadStatus.COMPLETED, DownloadStatus.FAILED}:
            return current
        now = self._now()
        try:
            signal = self.client.get_task_signal(task.btih, task.destination_path)
        except CloudDriveError as exc:
            target = transition_status(
                current,
                None,
                evidence_reliable=False,
                now=now,
                attention_after=task.attention_after_at,
            )
            self.repository.append_download_event(
                task_id,
                target,
                {"kind": "poll_error"},
                occurred_at=now,
                error_code=exc.code,
                error_summary=exc.code,
            )
            return target

        if signal is TaskSignal.FINISHED:
            return self._transition(
                task,
                DownloadStatus.COMPLETED,
                "task_api_finished",
                now,
                ownership_acquired=True,
            )
        if signal is TaskSignal.ERROR:
            return self._transition(task, DownloadStatus.FAILED, "external_task_failed", now)
        if signal is TaskSignal.DOWNLOADING:
            return self._transition(
                task,
                DownloadStatus.DOWNLOADING,
                "task_api_downloading",
                now,
                ownership_acquired=True,
            )
        if signal is TaskSignal.INIT:
            target = (
                DownloadStatus.SUBMITTED
                if current is DownloadStatus.SUBMITTING
                else current
            )
            return self._transition(
                task, target, "task_api_init", now, ownership_acquired=True
            )

        if task.completion_state is None or not task.destination_path:
            target = transition_status(
                current,
                None,
                now=now,
                attention_after=task.attention_after_at,
            )
            return self._transition(task, target, "completion_baseline_unavailable", now)
        try:
            current_snapshot = self.client.snapshot(
                task.destination_path, force_refresh=True
            )
        except CloudDriveError as exc:
            target = transition_status(
                current,
                None,
                evidence_reliable=False,
                now=now,
                attention_after=task.attention_after_at,
            )
            self.repository.append_download_event(
                task.id,
                target,
                {"kind": "snapshot_error"},
                occurred_at=now,
                error_code=exc.code,
                error_summary=exc.code,
            )
            return target

        observation = observe_completion(task.completion_state, current_snapshot)
        if observation.completed:
            target = DownloadStatus.COMPLETED
            kind = "stable_files_completed"
        elif observation.changed_file_count > 0 and observation.changed_total_size > 0:
            target = DownloadStatus.DOWNLOADING
            kind = "file_changes_observed"
        else:
            target = transition_status(
                current,
                None,
                now=now,
                attention_after=task.attention_after_at,
            )
            kind = "no_reliable_progress"
        target = transition_status(current, target)
        evidence = {
            "kind": kind,
            "changed_file_count": observation.changed_file_count,
            "changed_total_size": observation.changed_total_size,
        }
        if kind == "no_reliable_progress":
            latest = self.repository.get_latest_download_event(task.id)
            if (
                latest is not None
                and latest.status == target
                and latest.source == "system"
                and latest.evidence == evidence
                and observation.state == task.completion_state
            ):
                return target
        self.repository.append_download_event(
            task.id,
            target,
            evidence,
            occurred_at=now,
            completion_state=observation.state,
            ownership_acquired=bool(observation.state.owned_paths),
        )
        return target

    def manual_complete(self, task_id: str, *, confirmed: bool) -> None:
        if not confirmed:
            raise ConfirmationRequired("manual completion requires confirmation")
        task = self._require_task(task_id)
        target = transition_status(
            DownloadStatus(task.status), DownloadStatus.COMPLETED
        )
        now = self._now()
        self.repository.append_download_event(
            task_id,
            target,
            {"kind": "manual_confirmation"},
            source="manual",
            occurred_at=now,
        )
        self.repository.add_audit_event(
            "manual_download_completion",
            entity_type="download_task",
            entity_id=task_id,
            occurred_at=now,
        )

    def retry(self, task_id: str, *, confirmed: bool) -> str:
        task = self._require_task(task_id)
        status = DownloadStatus(task.status)
        forced = status is not DownloadStatus.FAILED
        if forced and not confirmed:
            raise ConfirmationRequired("forced retry requires confirmation")
        generation = self.repository.next_download_generation(
            task.download_client_key, task.content_item_id, task.candidate_id
        )
        retry_id = self.submit_candidate(
            profile_id=task.profile_id,
            content_item_id=task.content_item_id,
            candidate_id=task.candidate_id,
            magnet_uri="magnet:?xt=urn:btih:" + task.btih,
            destination=task.destination_path,
            generation=generation,
            supersedes_task_id=task.id,
        )
        if forced:
            self.repository.add_audit_event(
                "forced_download_retry",
                entity_type="download_task",
                entity_id=retry_id,
                details={"supersedes_task_id": task.id},
                occurred_at=self._now(),
            )
        return retry_id

    def _transition(
        self,
        task,
        target: DownloadStatus,
        kind: str,
        now: datetime,
        *,
        ownership_acquired: bool | None = None,
    ) -> DownloadStatus:
        current = DownloadStatus(task.status)
        target = transition_status(
            current,
            target,
            now=now,
            attention_after=task.attention_after_at,
        )
        self.repository.append_download_event(
            task.id,
            target,
            {"kind": kind},
            occurred_at=now,
            ownership_acquired=ownership_acquired,
        )
        return target

    def _require_task(self, task_id: str):
        task = self.repository.get_download_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task
