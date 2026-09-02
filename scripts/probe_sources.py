#!/usr/bin/env python3
"""Low-frequency live probes for Tuntu's planned v0.1 public sources.

The command reports only response metadata, counts, and field names. It never
prints titles, content identifiers, magnet URIs, or response bodies.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USER_AGENT = "Tuntu/0.1 source-probe"
MAX_RESPONSE_BYTES = 3_000_000


@dataclass(frozen=True, slots=True)
class ProbeResponse:
    status: int
    content_type: str
    body: bytes


class ProbeError(RuntimeError):
    pass


def extract_javdb_detail_paths(body: bytes) -> list[str]:
    matches = re.findall(rb'href="(/v/[^"?]+)', body)
    return list(dict.fromkeys(match.decode("ascii", errors="ignore") for match in matches))


def summarize_javdb_ranking(body: bytes) -> dict[str, int]:
    challenge_markers = re.findall(
        rb"cloudflare|captcha|verify you are human|just a moment",
        body,
        re.IGNORECASE,
    )
    return {
        "unique_detail_links": len(extract_javdb_detail_paths(body)),
        "challenge_markers": len(challenge_markers),
    }


def summarize_javdb_detail(body: bytes) -> dict[str, int]:
    return {
        "magnet_uris": len(re.findall(rb"magnet:\?xt=urn:btih:", body, re.IGNORECASE)),
        "candidate_rows": len(
            re.findall(rb'class="[^"]*\bmagnet-name\b', body, re.IGNORECASE)
        ),
    }


def summarize_javdatabase_feed(body: bytes) -> dict[str, int]:
    codes = set(re.findall(rb"\b[A-Z]{2,8}-[0-9]{2,6}\b", body, re.IGNORECASE))
    return {
        "items": len(re.findall(rb"<item>", body, re.IGNORECASE)),
        "unique_code_shapes": len(codes),
    }


def summarize_sukebei_feed(body: bytes) -> dict[str, int]:
    return {
        "items": len(re.findall(rb"<item>", body, re.IGNORECASE)),
        "infohash_fields": len(re.findall(rb"<nyaa:infoHash>", body, re.IGNORECASE)),
        "seeder_fields": len(re.findall(rb"<nyaa:seeders>", body, re.IGNORECASE)),
    }


def summarize_knaben(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    results = payload.get("hits") if isinstance(payload.get("hits"), list) else []
    return {
        "root_keys": sorted(payload),
        "results": len(results),
        "result_keys": sorted(results[0]) if results and isinstance(results[0], dict) else [],
    }


def summarize_bitsearch(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    return {
        "root_keys": sorted(payload),
        "success": payload.get("success"),
        "results": len(results),
        "result_keys": sorted(results[0]) if results and isinstance(results[0], dict) else [],
    }


def fetch(url: str, *, json_body: dict[str, Any] | None = None) -> ProbeResponse:
    headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
    data = None
    method = "GET"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return ProbeResponse(
                status=response.status,
                content_type=response.headers.get_content_type(),
                body=response.read(MAX_RESPONSE_BYTES),
            )
    except HTTPError as exc:
        return ProbeResponse(
            status=exc.code,
            content_type=exc.headers.get_content_type(),
            body=exc.read(MAX_RESPONSE_BYTES),
        )
    except URLError as exc:
        raise ProbeError(type(exc.reason).__name__) from exc


def fetch_json(url: str, *, json_body: dict[str, Any] | None = None) -> tuple[ProbeResponse, Any]:
    response = fetch(url, json_body=json_body)
    try:
        return response, json.loads(response.body)
    except json.JSONDecodeError as exc:
        raise ProbeError("invalid_json") from exc


def emit(source: str, response: ProbeResponse, summary: dict[str, Any]) -> None:
    result = {
        "source": source,
        "http": response.status,
        "content_type": response.content_type,
        "bytes_read": len(response.body),
        **summary,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def run(query: str) -> None:
    ranking = fetch("https://javdb.com/rankings/movies?p=weekly")
    emit("javdb_ranking", ranking, summarize_javdb_ranking(ranking.body))

    paths = extract_javdb_detail_paths(ranking.body)
    if paths:
        detail = fetch("https://javdb.com" + paths[0])
        emit("javdb_detail", detail, summarize_javdb_detail(detail.body))

    javdatabase = fetch("https://www.javdatabase.com/category/top-jav-movies/feed/")
    emit(
        "javdatabase_feed",
        javdatabase,
        summarize_javdatabase_feed(javdatabase.body),
    )

    sukebei_url = "https://sukebei.nyaa.si/?" + urlencode({"page": "rss", "q": query})
    sukebei = fetch(sukebei_url)
    emit("sukebei_rss", sukebei, summarize_sukebei_feed(sukebei.body))

    knaben_response, knaben = fetch_json(
        "https://api.knaben.org/v1",
        json_body={
            "search_type": "100%",
            "search_field": "title",
            "query": query,
            "order_by": "seeders",
            "order_direction": "desc",
            "size": 5,
            "hide_unsafe": True,
            "hide_xxx": False,
        },
    )
    emit("knaben_api", knaben_response, summarize_knaben(knaben))

    bitsearch_url = "https://bitsearch.to/api/v1/search?" + urlencode(
        {
            "q": query,
            "category": 10,
            "sort": "seeders",
            "order": "desc",
            "limit": 5,
        }
    )
    bitsearch_response, bitsearch = fetch_json(bitsearch_url)
    emit("bitsearch_api", bitsearch_response, summarize_bitsearch(bitsearch))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        required=True,
        help="A content identifier to check. The value is never printed.",
    )
    arguments = parser.parse_args()
    try:
        run(arguments.query)
    except ProbeError as exc:
        raise SystemExit(f"source probe failed: {exc}") from exc


if __name__ == "__main__":
    main()
