from __future__ import annotations

from collections.abc import Iterable

from .contracts import CandidateSource, DiscoverySource
from .models import Candidate, ContentItem, ContentResult, ContentResultStatus
from .rules import RuleSet
from .selector import evaluation_sort_key, select_candidate


class Pipeline:
    def __init__(
        self,
        discovery_sources: Iterable[DiscoverySource],
        candidate_sources: Iterable[CandidateSource],
        rules: RuleSet | None = None,
    ):
        self.discovery_sources = list(discovery_sources)
        self.candidate_sources = list(candidate_sources)
        self.rules = rules or RuleSet()

    def collect_items(self, scope: str, *, run_id: str = "adhoc") -> list[ContentItem]:
        merged: dict[tuple[str, str], ContentItem] = {}
        for source in self.discovery_sources:
            for item in source.collect(scope, run_id=run_id):
                if item.identity in merged:
                    merged[item.identity].merge_from(item)
                else:
                    merged[item.identity] = item
        return sorted(merged.values(), key=lambda item: (item.best_rank, item.identity))

    def discover(self, scope: str, *, run_id: str = "adhoc") -> list[ContentResult]:
        results: list[ContentResult] = []
        for item in self.collect_items(scope, run_id=run_id):
            candidates: dict[str, Candidate] = {}
            for source in self.candidate_sources:
                for candidate in source.search(item, run_id=run_id):
                    if candidate.item_identity != item.identity:
                        raise ValueError("candidate does not belong to the searched content item")
                    if candidate.btih in candidates:
                        candidates[candidate.btih].merge_from(candidate)
                    else:
                        candidates[candidate.btih] = candidate

            evaluations = [self.rules.evaluate(candidate) for candidate in candidates.values()]
            evaluations.sort(key=evaluation_sort_key)
            selected = select_candidate(evaluations)
            if not evaluations:
                status = ContentResultStatus.NO_CANDIDATE
            elif selected is None:
                status = ContentResultStatus.FILTERED
            else:
                status = ContentResultStatus.SELECTED
            results.append(
                ContentResult(
                    item=item,
                    status=status,
                    evaluations=evaluations,
                    selected=selected,
                )
            )
        return results
