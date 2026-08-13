from __future__ import annotations

from collections.abc import Iterable

from .contracts import CandidateSource, DiscoverySource, DownloadRoute, Rule
from .models import Candidate, ContentItem, DownloadReceipt, Evaluation


class Pipeline:
    def __init__(
        self,
        discovery_sources: Iterable[DiscoverySource],
        candidate_sources: Iterable[CandidateSource],
        rules: Iterable[Rule] = (),
        routes: Iterable[DownloadRoute] = (),
    ):
        self.discovery_sources = list(discovery_sources)
        self.candidate_sources = list(candidate_sources)
        self.rules = list(rules)
        self.routes = list(routes)

    def collect_items(self, scope: str) -> list[ContentItem]:
        merged: dict[str, ContentItem] = {}
        for source in self.discovery_sources:
            for item in source.collect(scope):
                key = item.key.casefold()
                if key in merged:
                    merged[key].merge_from(item)
                else:
                    merged[key] = item
        return sorted(merged.values(), key=lambda item: item.priority)

    def discover(self, scope: str) -> tuple[list[ContentItem], list[Evaluation]]:
        items = self.collect_items(scope)
        candidates: dict[str, Candidate] = {}
        for item in items:
            for source in self.candidate_sources:
                for candidate in source.search(item):
                    identity = candidate.identity.casefold()
                    if identity in candidates:
                        candidates[identity].merge_from(candidate)
                    else:
                        candidates[identity] = candidate

        evaluations: list[Evaluation] = []
        for candidate in candidates.values():
            reasons = [reason for rule in self.rules if (reason := rule.reject_reason(candidate))]
            evaluations.append(Evaluation(candidate=candidate, accepted=not reasons, reasons=reasons))
        evaluations.sort(
            key=lambda result: (
                result.accepted,
                result.candidate.seeders,
                result.candidate.size_mb,
            ),
            reverse=True,
        )
        return items, evaluations

    def submit(self, evaluations: Iterable[Evaluation]) -> list[DownloadReceipt]:
        return self.submit_candidates(
            result.candidate for result in evaluations if result.accepted
        )

    def submit_candidates(self, candidates: Iterable[Candidate]) -> list[DownloadReceipt]:
        receipts: list[DownloadReceipt] = []
        for candidate in candidates:
            route = next((route for route in self.routes if route.accepts(candidate)), None)
            if route is None:
                receipts.append(
                    DownloadReceipt(candidate.identity, "skipped", message="no download route matched")
                )
                continue
            receipts.append(route.submit(candidate))
        return receipts
