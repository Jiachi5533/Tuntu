from __future__ import annotations

from dataclasses import dataclass, field

from .models import Candidate


@dataclass(slots=True)
class MinSeeders:
    value: int
    name: str = "min_seeders"

    def reject_reason(self, candidate: Candidate) -> str | None:
        if candidate.seeders < self.value:
            return f"seeders {candidate.seeders} < {self.value}"
        return None


@dataclass(slots=True)
class SizeRange:
    min_mb: float | None = None
    max_mb: float | None = None
    name: str = "size_range"

    def reject_reason(self, candidate: Candidate) -> str | None:
        if self.min_mb is not None and candidate.size_mb < self.min_mb:
            return f"size {candidate.size_mb} MB < {self.min_mb} MB"
        if self.max_mb is not None and candidate.size_mb > self.max_mb:
            return f"size {candidate.size_mb} MB > {self.max_mb} MB"
        return None


@dataclass(slots=True)
class TagPolicy:
    require_all: set[str] = field(default_factory=set)
    exclude_any: set[str] = field(default_factory=set)
    name: str = "tag_policy"

    def reject_reason(self, candidate: Candidate) -> str | None:
        missing = self.require_all - candidate.tags
        if missing:
            return f"missing tags: {', '.join(sorted(missing))}"
        excluded = self.exclude_any & candidate.tags
        if excluded:
            return f"excluded tags: {', '.join(sorted(excluded))}"
        return None


@dataclass(slots=True)
class KeywordPolicy:
    include_any: tuple[str, ...] = ()
    exclude_any: tuple[str, ...] = ()
    name: str = "keyword_policy"

    def reject_reason(self, candidate: Candidate) -> str | None:
        title = candidate.title.casefold()
        if self.include_any and not any(value.casefold() in title for value in self.include_any):
            return "no required keyword matched"
        matched = [value for value in self.exclude_any if value.casefold() in title]
        if matched:
            return f"excluded keyword: {matched[0]}"
        return None

