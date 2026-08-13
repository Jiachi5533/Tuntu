from __future__ import annotations

from typing import Protocol

from .models import Candidate, DownloadReceipt, ContentItem


class DiscoverySource(Protocol):
    name: str

    def collect(self, scope: str) -> list[ContentItem]: ...


class CandidateSource(Protocol):
    name: str

    def search(self, item: ContentItem) -> list[Candidate]: ...


class Rule(Protocol):
    name: str

    def reject_reason(self, candidate: Candidate) -> str | None: ...


class DownloadClient(Protocol):
    name: str

    def submit(self, candidate: Candidate, destination: str) -> DownloadReceipt: ...


class DownloadRoute(Protocol):
    name: str

    def accepts(self, candidate: Candidate) -> bool: ...

    def submit(self, candidate: Candidate) -> DownloadReceipt: ...
