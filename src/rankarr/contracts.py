from __future__ import annotations

from typing import Protocol

from .models import Candidate, DownloadReceipt, RankedItem


class RankingSource(Protocol):
    name: str

    def fetch(self, period: str) -> list[RankedItem]: ...


class CandidateSource(Protocol):
    name: str

    def search(self, item: RankedItem) -> list[Candidate]: ...


class Rule(Protocol):
    name: str

    def reject_reason(self, candidate: Candidate) -> str | None: ...


class DownloadClient(Protocol):
    name: str

    def submit(self, candidate: Candidate, destination: str) -> DownloadReceipt: ...

