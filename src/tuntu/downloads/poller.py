from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PollResult:
    attempted: int
    succeeded: int
    failed: int


class DownloadPoller:
    def __init__(self, repository, download_service):
        self.repository = repository
        self.download_service = download_service

    def poll_once(self) -> PollResult:
        task_ids = self.repository.list_unfinished_download_task_ids()
        succeeded = 0
        failed = 0
        for task_id in task_ids:
            try:
                self.download_service.poll(task_id)
            except Exception:
                failed += 1
                self.repository.add_audit_event(
                    "download_poll_failed",
                    entity_type="download_task",
                    entity_id=task_id,
                    details={"error_code": "poll_unexpected_error"},
                )
            else:
                succeeded += 1
        return PollResult(len(task_ids), succeeded, failed)
