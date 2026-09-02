from __future__ import annotations

import xml.etree.ElementTree as ET

from tuntu.magnet import InvalidMagnet
from tuntu.models import CandidateEvidence, ContentItem
from tuntu.normalization import candidate_from_magnet

from .attributes import classify_attributes, parse_size_mb
from .errors import ProviderParseError
from .http import ProviderHttpClient


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class SukebeiCandidateProvider:
    name = "sukebei_rss"
    kind = "candidate"

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        feed_url: str = "https://sukebei.nyaa.si/",
    ):
        self.http = http
        self.feed_url = feed_url

    def search(self, item: ContentItem, *, run_id: str):
        response = self.http.get(
            self.feed_url,
            params={"page": "rss", "q": item.normalized_key},
            run_id=run_id,
        )
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ProviderParseError("invalid_xml") from exc
        channel = root.find("channel")
        if channel is None:
            raise ProviderParseError("structure_changed")
        entries = channel.findall("item")
        candidates = []
        for entry in entries:
            fields = {_local_name(child.tag).casefold(): (child.text or "").strip() for child in entry}
            info_hash = fields.get("infohash", "")
            title = fields.get("title", "")
            if not info_hash or not title:
                continue
            chinese, uncensored, uhd = classify_attributes(title)
            try:
                seeders = int(fields["seeders"]) if fields.get("seeders") else None
            except ValueError:
                seeders = None
            evidence = CandidateEvidence(
                source=self.name,
                magnet_uri=f"magnet:?xt=urn:btih:{info_hash}",
                title=title,
                size_mb=parse_size_mb(fields.get("size", "")),
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
        if entries and not candidates:
            raise ProviderParseError("invalid_candidate_data")
        return candidates
