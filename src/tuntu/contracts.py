from __future__ import annotations

from typing import Protocol

from .models import Candidate, ContentItem


class DiscoverySource(Protocol):
    name: str

    def collect(self, scope: str, *, run_id: str) -> list[ContentItem]: ...


class CandidateSource(Protocol):
    name: str

    def search(self, item: ContentItem, *, run_id: str) -> list[Candidate]: ...


class DownloadClientConfig(Protocol):
    attention_after_seconds: int
    required_stable_observations: int


class DownloadClient(Protocol):
    name: str
    config: DownloadClientConfig

    def health_check(self) -> object: ...

    def ensure_destination(self, destination: str) -> None: ...

    def submit(self, magnet_uri: str, destination: str) -> object: ...

    def get_task_signal(self, btih: str, destination: str) -> object: ...

    def snapshot(self, destination: str, *, force_refresh: bool) -> object: ...

    def close(self) -> None: ...
