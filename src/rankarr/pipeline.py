from __future__ import annotations

from collections.abc import Iterable

from .contracts import CandidateSource, DownloadClient, RankingSource, Rule
from .models import Candidate, DownloadReceipt, Evaluation, RankedItem


class Pipeline:
    def __init__(
        self,
        ranking_sources: Iterable[RankingSource],
        candidate_sources: Iterable[CandidateSource],
        rules: Iterable[Rule] = (),
        download_client: DownloadClient | None = None,
    ):
        self.ranking_sources = list(ranking_sources)
        self.candidate_sources = list(candidate_sources)
        self.rules = list(rules)
        self.download_client = download_client

    def collect_rankings(self, period: str) -> list[RankedItem]:
        merged: dict[str, RankedItem] = {}
        for source in self.ranking_sources:
            for item in source.fetch(period):
                key = item.key.casefold()
                if key in merged:
                    merged[key].merge_from(item)
                else:
                    merged[key] = item
        return sorted(merged.values(), key=lambda item: item.rank)

    def discover(self, period: str) -> tuple[list[RankedItem], list[Evaluation]]:
        rankings = self.collect_rankings(period)
        candidates: dict[str, Candidate] = {}
        for item in rankings:
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
        return rankings, evaluations

    def submit(self, evaluations: Iterable[Evaluation], destination: str) -> list[DownloadReceipt]:
        if self.download_client is None:
            raise RuntimeError("download client is not configured")
        return [
            self.download_client.submit(result.candidate, destination)
            for result in evaluations
            if result.accepted
        ]

