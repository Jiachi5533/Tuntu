from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TruthValue(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RankingEvidence:
    source: str
    rank: int
    raw_key: str
    scope: str = ""

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("ranking source cannot be empty")
        if self.rank < 1:
            raise ValueError("ranking rank must be positive")
        if not self.raw_key.strip():
            raise ValueError("ranking raw_key cannot be empty")


@dataclass(slots=True)
class ContentItem:
    """A content-neutral wanted item with provider-normalized identity."""

    namespace: str
    raw_key: str
    normalized_key: str
    rankings: list[RankingEvidence]
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.namespace.strip():
            raise ValueError("content namespace cannot be empty")
        if not self.raw_key.strip():
            raise ValueError("content raw_key cannot be empty")
        if not self.normalized_key.strip():
            raise ValueError("content normalized_key cannot be empty")
        if not self.rankings:
            raise ValueError("content must include ranking evidence")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.namespace.strip().casefold(), self.normalized_key.strip().casefold())

    @property
    def best_rank(self) -> int:
        return min(entry.rank for entry in self.rankings)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(
            sorted({entry.source for entry in self.rankings}, key=lambda value: (value.casefold(), value))
        )

    def merge_from(self, other: ContentItem) -> None:
        if self.identity != other.identity:
            raise ValueError("cannot merge different content identities")
        for entry in other.rankings:
            if entry not in self.rankings:
                self.rankings.append(entry)
        if (len(other.title), other.title.casefold(), other.title) > (
            len(self.title),
            self.title.casefold(),
            self.title,
        ):
            self.title = other.title
        self.metadata.update(other.metadata)


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """One source's normalized observations for a magnet candidate."""

    source: str
    magnet_uri: str
    title: str = ""
    size_mb: float | None = None
    seeders: int | None = None
    chinese_subtitles: TruthValue = TruthValue.UNKNOWN
    uncensored: TruthValue = TruthValue.UNKNOWN
    uhd: TruthValue = TruthValue.UNKNOWN
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("candidate source cannot be empty")
        if not self.magnet_uri.strip():
            raise ValueError("candidate magnet_uri cannot be empty")
        if self.size_mb is not None and self.size_mb < 0:
            raise ValueError("candidate size_mb cannot be negative")
        if self.seeders is not None and self.seeders < 0:
            raise ValueError("candidate seeders cannot be negative")
        for field_name in ("chinese_subtitles", "uncensored", "uhd"):
            if not isinstance(getattr(self, field_name), TruthValue):
                raise ValueError(f"candidate {field_name} must be a TruthValue")


@dataclass(slots=True)
class Candidate:
    """A v1 magnet identified by normalized BTIH with all source evidence."""

    item_identity: tuple[str, str]
    btih: str
    evidence: list[CandidateEvidence]

    def __post_init__(self) -> None:
        if len(self.item_identity) != 2 or not all(part for part in self.item_identity):
            raise ValueError("candidate item_identity is invalid")
        if len(self.btih) != 40 or any(char not in "0123456789abcdef" for char in self.btih):
            raise ValueError("candidate btih must be normalized lowercase hexadecimal")
        if not self.evidence:
            raise ValueError("candidate must include source evidence")

    @property
    def identity(self) -> str:
        return self.btih

    @property
    def magnet_uri(self) -> str:
        return f"magnet:?xt=urn:btih:{self.btih}"

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(
            sorted({entry.source for entry in self.evidence}, key=lambda value: (value.casefold(), value))
        )

    @property
    def title(self) -> str:
        titles = {entry.title for entry in self.evidence if entry.title}
        if not titles:
            return ""
        return max(titles, key=lambda value: (len(value), value.casefold(), value))

    @property
    def search_text(self) -> str:
        return "\n".join(
            sorted(
                {entry.title for entry in self.evidence if entry.title},
                key=lambda value: (value.casefold(), value),
            )
        )

    @property
    def size_mb(self) -> float | None:
        values = {entry.size_mb for entry in self.evidence if entry.size_mb is not None}
        return next(iter(values)) if len(values) == 1 else None

    @property
    def seeders(self) -> int | None:
        values = [entry.seeders for entry in self.evidence if entry.seeders is not None]
        return max(values) if values else None

    def _truth_value(self, field_name: str) -> TruthValue:
        values = {
            getattr(entry, field_name)
            for entry in self.evidence
            if getattr(entry, field_name) is not TruthValue.UNKNOWN
        }
        return next(iter(values)) if len(values) == 1 else TruthValue.UNKNOWN

    @property
    def chinese_subtitles(self) -> TruthValue:
        return self._truth_value("chinese_subtitles")

    @property
    def uncensored(self) -> TruthValue:
        return self._truth_value("uncensored")

    @property
    def uhd(self) -> TruthValue:
        return self._truth_value("uhd")

    def merge_from(self, other: Candidate) -> None:
        if (self.item_identity, self.btih) != (other.item_identity, other.btih):
            raise ValueError("cannot merge different candidates")
        for entry in other.evidence:
            if entry not in self.evidence:
                self.evidence.append(entry)


@dataclass(frozen=True, slots=True)
class RuleReason:
    code: str
    message: str


@dataclass(slots=True)
class Evaluation:
    candidate: Candidate
    accepted: bool
    reasons: list[RuleReason] = field(default_factory=list)


class ContentResultStatus(StrEnum):
    SELECTED = "selected"
    NO_CANDIDATE = "no_candidate"
    FILTERED = "filtered"


@dataclass(slots=True)
class ContentResult:
    item: ContentItem
    status: ContentResultStatus
    evaluations: list[Evaluation] = field(default_factory=list)
    selected: Candidate | None = None
