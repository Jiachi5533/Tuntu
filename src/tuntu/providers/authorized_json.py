from __future__ import annotations

import json

from tuntu.magnet import InvalidMagnet
from tuntu.models import CandidateEvidence, ContentItem, TruthValue
from tuntu.normalization import candidate_from_magnet

from .attributes import classify_attributes
from .errors import ProviderParseError
from .http import ProviderHttpClient


class AuthorizedJsonCandidateProvider:
    """Candidate adapter for an operator-controlled or licensed HTTP API."""

    name = "authorized_json_api"
    kind = "candidate"

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        endpoint: str,
        api_token: str | None = None,
    ):
        self.http = http
        self.endpoint = endpoint
        self.api_token = api_token

    def search(self, item: ContentItem, *, run_id: str):
        headers = (
            {"Authorization": f"Bearer {self.api_token}"}
            if self.api_token
            else None
        )
        response = self.http.post(
            self.endpoint,
            headers=headers,
            json_body={
                "namespace": item.namespace,
                "key": item.normalized_key,
                "raw_key": item.raw_key,
                "title": item.title,
            },
            run_id=run_id,
        )
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderParseError("invalid_json") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ProviderParseError("structure_changed")

        candidates = []
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            magnet_uri = result.get("magnet_uri")
            title = result.get("title", "")
            if not isinstance(magnet_uri, str) or not isinstance(title, str):
                continue
            size_mb = self._number(result.get("size_mb"))
            seeders = self._integer(result.get("seeders"))
            classified = classify_attributes(title)
            evidence = CandidateEvidence(
                source=self.name,
                magnet_uri=magnet_uri,
                title=title,
                size_mb=size_mb,
                seeders=seeders,
                chinese_subtitles=self._truth(
                    result.get("chinese_subtitles"), classified[0]
                ),
                uncensored=self._truth(result.get("uncensored"), classified[1]),
                uhd=self._truth(result.get("uhd"), classified[2]),
            )
            try:
                candidates.append(
                    candidate_from_magnet(item_identity=item.identity, evidence=evidence)
                )
            except (InvalidMagnet, ValueError):
                continue
        if payload["results"] and not candidates:
            raise ProviderParseError("invalid_candidate_data")
        return candidates

    @staticmethod
    def _number(value) -> float | None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            return float(value)
        return None

    @staticmethod
    def _integer(value) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    @staticmethod
    def _truth(value, fallback: TruthValue) -> TruthValue:
        try:
            return TruthValue(value) if value is not None else fallback
        except ValueError:
            return fallback
