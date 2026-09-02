from __future__ import annotations

from datetime import datetime
from enum import StrEnum


class DownloadStatus(StrEnum):
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    ATTENTION_REQUIRED = "attention_required"


class InvalidStatusTransition(ValueError):
    pass


_ALLOWED = {
    DownloadStatus.SUBMITTING: {
        DownloadStatus.SUBMITTED,
        DownloadStatus.DOWNLOADING,
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
        DownloadStatus.ATTENTION_REQUIRED,
    },
    DownloadStatus.SUBMITTED: {
        DownloadStatus.DOWNLOADING,
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
        DownloadStatus.ATTENTION_REQUIRED,
    },
    DownloadStatus.DOWNLOADING: {
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
        DownloadStatus.ATTENTION_REQUIRED,
    },
    DownloadStatus.ATTENTION_REQUIRED: {
        DownloadStatus.SUBMITTED,
        DownloadStatus.DOWNLOADING,
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
    },
    DownloadStatus.COMPLETED: set(),
    DownloadStatus.FAILED: set(),
}


def transition_status(
    current: DownloadStatus,
    target: DownloadStatus | None,
    *,
    evidence_reliable: bool = True,
    now: datetime | None = None,
    attention_after: datetime | None = None,
) -> DownloadStatus:
    current = DownloadStatus(current)
    if target is None or not evidence_reliable:
        if (
            attention_after is not None
            and now is not None
            and now >= attention_after
            and current not in {DownloadStatus.COMPLETED, DownloadStatus.FAILED}
        ):
            return DownloadStatus.ATTENTION_REQUIRED
        return current
    target = DownloadStatus(target)
    if target == current:
        return current
    if target not in _ALLOWED[current]:
        raise InvalidStatusTransition(f"{current.value} -> {target.value}")
    return target
