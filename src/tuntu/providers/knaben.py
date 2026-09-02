from __future__ import annotations

import json

from tuntu.magnet import InvalidMagnet
from tuntu.models import CandidateEvidence, ContentItem
from tuntu.normalization import candidate_from_magnet

from .attributes import classify_attributes
from .errors import ProviderParseError
from .http import ProviderHttpClient


class KnabenCandidateProvider:
    name = "knaben_api"
    kind = "candidate"

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        endpoint: str = "https://api.knaben.org/v1",
        result_limit: int = 20,
    ):
        self.http = http
        self.endpoint = endpoint
        self.result_limit = result_limit

    def search(self, item: ContentItem, *, run_id: str):
        response = self.http.post(
            self.endpoint,
            json_body={
                "search_type": "100%",
                "search_field": "title",
                "query": item.normalized_key,
                "order_by": "seeders",
                "order_direction": "desc",
                "size": self.result_limit,
                "hide_unsafe": True,
                "hide_xxx": False,
            },
            run_id=run_id,
        )
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderParseError("invalid_json") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
            raise ProviderParseError("structure_changed")

        candidates = []
        for hit in payload["hits"]:
            if not isinstance(hit, dict):
                continue
            title = hit.get("title") if isinstance(hit.get("title"), str) else ""
            magnet_uri = hit.get("magnetUrl") if isinstance(hit.get("magnetUrl"), str) else ""
            info_hash = hit.get("hash") if isinstance(hit.get("hash"), str) else ""
            if not magnet_uri and info_hash:
                magnet_uri = f"magnet:?xt=urn:btih:{info_hash}"
            if not title or not magnet_uri:
                continue
            raw_bytes = hit.get("bytes")
            size_mb = (
                round(raw_bytes / (1024 * 1024), 3)
                if isinstance(raw_bytes, (int, float)) and not isinstance(raw_bytes, bool) and raw_bytes >= 0
                else None
            )
            raw_seeders = hit.get("seeders")
            seeders = raw_seeders if isinstance(raw_seeders, int) and not isinstance(raw_seeders, bool) and raw_seeders >= 0 else None
            chinese, uncensored, uhd = classify_attributes(title)
            evidence = CandidateEvidence(
                source=self.name,
                magnet_uri=magnet_uri,
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
        if payload["hits"] and not candidates:
            raise ProviderParseError("invalid_candidate_data")
        return candidates
