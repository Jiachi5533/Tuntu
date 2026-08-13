from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RankedItem:
    """A content item discovered in one or more rankings."""

    key: str
    rank: int
    sources: list[str]
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def merge_from(self, other: RankedItem) -> None:
        self.rank = min(self.rank, other.rank)
        for source in other.sources:
            if source not in self.sources:
                self.sources.append(source)
        if not self.title and other.title:
            self.title = other.title
        self.metadata.update(other.metadata)


@dataclass(slots=True)
class Candidate:
    """A downloadable candidate produced for a ranked item."""

    identity: str
    item_key: str
    title: str
    download_url: str
    sources: list[str]
    size_mb: float = 0
    seeders: int = 0
    tags: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    def merge_from(self, other: Candidate) -> None:
        for source in other.sources:
            if source not in self.sources:
                self.sources.append(source)
        self.size_mb = max(self.size_mb, other.size_mb)
        self.seeders = max(self.seeders, other.seeders)
        self.tags.update(other.tags)
        self.metadata.update(other.metadata)
        if len(other.title) > len(self.title):
            self.title = other.title


@dataclass(slots=True)
class Evaluation:
    candidate: Candidate
    accepted: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DownloadReceipt:
    candidate_identity: str
    status: str
    external_id: str = ""
    message: str = ""

