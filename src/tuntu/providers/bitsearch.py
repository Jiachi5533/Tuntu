from __future__ import annotations

import json

from tuntu.magnet import InvalidMagnet
from tuntu.models import CandidateEvidence, ContentItem
from tuntu.normalization import candidate_from_magnet

from .attributes import classify_attributes
from .errors import ProviderParseError
from .http import ProviderHttpClient


class BitsearchCandidateProvider:
    name = "bitsearch_api"
    kind = "candidate"

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        endpoint: str = "https://bitsearch.to/api/v1/search",
        result_limit: int = 20,
        category: int | None = 10,
    ):
        self.http = http
        self.endpoint = endpoint
        self.result_limit = result_limit
        self.category = category

    def search(self, item: ContentItem, *, run_id: str):
        params = {
            "q": item.normalized_key,
            "sort": "seeders",
            "order": "desc",
            "limit": self.result_limit,
        }
        if self.category is not None:
            params["category"] = self.category
        response = self.http.get(self.endpoint, params=params, run_id=run_id)
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderParseError("invalid_json") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or not isinstance(payload.get("results"), list)
        ):
            raise ProviderParseError("structure_changed")

        candidates = []
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            title = result.get("title") if isinstance(result.get("title"), str) else ""
            info_hash = (
                result.get("infohash") if isinstance(result.get("infohash"), str) else ""
            )
            if not title or not info_hash:
                continue
            raw_size = result.get("size")
            size_mb = (
                round(raw_size / (1024 * 1024), 3)
                if isinstance(raw_size, (int, float))
                and not isinstance(raw_size, bool)
                and raw_size >= 0
                else None
            )
            raw_seeders = result.get("seeders")
            seeders = (
                raw_seeders
                if isinstance(raw_seeders, int)
                and not isinstance(raw_seeders, bool)
                and raw_seeders >= 0
                else None
            )
            chinese, uncensored, uhd = classify_attributes(title)
            evidence = CandidateEvidence(
                source=self.name,
                magnet_uri=f"magnet:?xt=urn:btih:{info_hash}",
                title=title,
                size_mb=size_mb,
                seeders=seeders,
                chinese_subtitles=chinese,
                uncensored=uncensored,
                uhd=uhd,
            )
            try:
                candidates.append(
                    candidate_from_magnet(item_identity=item.identity, evidence=evidence)
                )
            except InvalidMagnet:
                continue
        if payload["results"] and not candidates:
            raise ProviderParseError("invalid_candidate_data")
        return candidates
