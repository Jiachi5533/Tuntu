from __future__ import annotations

from tuntu.models import ContentItem, RankingEvidence

from .attributes import normalize_jav_identity


class ManualDiscoveryProvider:
    name = "manual"
    kind = "discovery"

    def __init__(self, raw_keys: list[str]):
        self.raw_keys = raw_keys

    def collect(self, scope: str, *, run_id: str) -> list[ContentItem]:
        items = []
        for rank, raw_key in enumerate(self.raw_keys, 1):
            namespace, normalized_key = normalize_jav_identity(
                raw_key, fallback_namespace="manual"
            )
            items.append(
                ContentItem(
                    namespace=namespace,
                    raw_key=raw_key,
                    normalized_key=normalized_key,
                    rankings=[
                        RankingEvidence(source=self.name, rank=rank, raw_key=raw_key, scope=scope)
                    ],
                    title=raw_key,
                )
            )
        return items
