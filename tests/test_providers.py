from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx

from tuntu.models import ContentItem, RankingEvidence, TruthValue
from tuntu.providers import (
    BitsearchCandidateProvider,
    AuthorizedJsonCandidateProvider,
    JavDatabaseRankingProvider,
    JavDbCandidateProvider,
    JavDbRankingProvider,
    KnabenCandidateProvider,
    ManualDiscoveryProvider,
    ProviderParseError,
    SukebeiCandidateProvider,
    normalize_jav_identity,
)
from tuntu.providers.http import ProviderHttpClient


FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def fixture_bytes(name):
    return (FIXTURES / name).read_bytes()


def make_http(routes):
    def handler(request):
        key = (request.method, request.url.path)
        status, content, content_type = routes[key]
        return httpx.Response(
            status,
            content=content,
            headers={"Content-Type": content_type},
            request=request,
        )

    return ProviderHttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_ttl_seconds=60,
    )


def make_item(raw_key="ABC-1", normalized_key="ABC-001", detail_path="/v/fixture-one"):
    return ContentItem(
        namespace="jav",
        raw_key=raw_key,
        normalized_key=normalized_key,
        rankings=[RankingEvidence(source="fixture", rank=1, raw_key=raw_key)],
        metadata={"javdb_detail_path": detail_path},
    )


class JavIdentityTests(unittest.TestCase):
    def test_normalizes_supported_codes_without_cross_namespace_guessing(self):
        self.assertEqual(normalize_jav_identity("abc_1"), ("jav", "ABC-001"))
        self.assertEqual(normalize_jav_identity("XYZ-00042"), ("jav", "XYZ-042"))
        self.assertEqual(
            normalize_jav_identity("FC2 PPV 1234567"),
            ("jav", "FC2-PPV-1234567"),
        )
        self.assertEqual(
            normalize_jav_identity("unstructured value", fallback_namespace="manual"),
            ("manual", "unstructured value"),
        )


class DiscoveryProviderTests(unittest.TestCase):
    def test_javdb_ranking_sends_configured_browser_session_headers(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                content=fixture_bytes("javdb_ranking.html"),
                headers={"Content-Type": "text/html"},
                request=request,
            )

        http = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        try:
            JavDbRankingProvider(
                http,
                cookie="fixture_session=member",
                user_agent="Fixture Browser/1.0",
            ).collect("weekly", run_id="run-member")
        finally:
            http.close()

        self.assertEqual(requests[0].headers["cookie"], "fixture_session=member")
        self.assertEqual(requests[0].headers["user-agent"], "Fixture Browser/1.0")
        self.assertEqual(requests[0].headers["accept-language"], "zh-CN,zh;q=0.9")

    def test_javdb_supports_daily_weekly_monthly_and_preserves_detail_path(self):
        seen_queries = []

        def handler(request):
            seen_queries.append(request.url.params.get("p"))
            return httpx.Response(
                200,
                content=fixture_bytes("javdb_ranking.html"),
                headers={"Content-Type": "text/html"},
                request=request,
            )

        http = ProviderHttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
        provider = JavDbRankingProvider(http)
        try:
            for scope in ("daily", "weekly", "monthly"):
                items = provider.collect(scope, run_id=f"run-{scope}")
                self.assertEqual([item.normalized_key for item in items], ["ABC-001", "XYZ-042"])
                self.assertEqual(items[0].metadata["javdb_detail_path"], "/v/fixture-one")
            self.assertEqual(seen_queries, ["daily", "weekly", "monthly"])
        finally:
            http.close()

    def test_javdatabase_fetches_the_latest_full_week_with_covers(self):
        http = make_http(
            {
                ("GET", "/category/top-jav-movies/feed/"): (
                    200,
                    fixture_bytes("javdatabase_feed.xml"),
                    "application/rss+xml",
                ),
                ("GET", "/top-week/"): (
                    200,
                    fixture_bytes("javdatabase_weekly.html"),
                    "text/html",
                ),
            }
        )
        try:
            items = JavDatabaseRankingProvider(
                http,
                feed_url="https://example.invalid/category/top-jav-movies/feed/",
            ).collect("weekly", run_id="run-1")
            self.assertEqual(
                [item.normalized_key for item in items],
                ["ABC-001", "XYZ-042", "QWE-003"],
            )
            self.assertEqual([item.best_rank for item in items], [1, 2, 3])
            self.assertEqual(items[0].title, "First fixture title")
            self.assertEqual(
                items[0].metadata,
                {
                    "cover_url": "https://example.invalid/covers/abc-1.webp",
                    "source_url": "https://example.invalid/movies/abc-1/",
                    "ranking_page_url": "https://example.invalid/top-week/",
                    "ranking_title": "Fixture week",
                },
            )
        finally:
            http.close()

    def test_javdatabase_rejects_cross_origin_article_links(self):
        feed = b"""<rss><channel><item><title>Week</title>
        <link>https://attacker.invalid/internal</link></item></channel></rss>"""
        http = make_http(
            {
                ("GET", "/category/top-jav-movies/feed/"): (
                    200,
                    feed,
                    "application/rss+xml",
                )
            }
        )
        try:
            with self.assertRaisesRegex(ProviderParseError, "unsafe_article_url"):
                JavDatabaseRankingProvider(http).collect("weekly", run_id="run-1")
        finally:
            http.close()

    def test_discovery_valid_empty_and_structure_change_are_distinct(self):
        http = make_http(
            {
                ("GET", "/rankings/movies"): (
                    200,
                    b'<div class="movie-list"></div>',
                    "text/html",
                ),
                ("GET", "/category/top-jav-movies/feed/"): (
                    200,
                    b"<rss><channel></channel></rss>",
                    "application/rss+xml",
                ),
            }
        )
        try:
            self.assertEqual(JavDbRankingProvider(http).collect("weekly", run_id="one"), [])
            self.assertEqual(
                JavDatabaseRankingProvider(http).collect("weekly", run_id="one"), []
            )
        finally:
            http.close()

        changed = make_http(
            {
                ("GET", "/rankings/movies"): (200, b"<html>changed</html>", "text/html"),
                ("GET", "/category/top-jav-movies/feed/"): (
                    200,
                    b"<rss><unexpected /></rss>",
                    "application/rss+xml",
                ),
            }
        )
        try:
            with self.assertRaises(ProviderParseError):
                JavDbRankingProvider(changed).collect("weekly", run_id="two")
            with self.assertRaises(ProviderParseError):
                JavDatabaseRankingProvider(changed).collect("weekly", run_id="two")
        finally:
            changed.close()

    def test_manual_input_uses_same_normalizer_and_ranking_shape(self):
        items = ManualDiscoveryProvider(["abc-1", "free form"]).collect(
            "manual", run_id="run-1"
        )

        self.assertEqual([item.identity for item in items], [("jav", "abc-001"), ("manual", "free form")])


class CandidateProviderTests(unittest.TestCase):
    def test_authorized_json_api_sends_bearer_token_and_parses_standard_candidates(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "magnet_uri": "magnet:?xt=urn:btih:" + "c" * 40,
                            "title": "Authorized fixture 4K 中文字幕",
                            "size_mb": 2048,
                            "seeders": 7,
                        }
                    ]
                },
                request=request,
            )

        http = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        try:
            candidates = AuthorizedJsonCandidateProvider(
                http,
                endpoint="https://authorized.example/api/candidates",
                api_token="fixture-secret",
            ).search(make_item(), run_id="run-authorized")
        finally:
            http.close()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].btih, "c" * 40)
        self.assertEqual(candidates[0].chinese_subtitles, TruthValue.YES)
        self.assertEqual(candidates[0].uhd, TruthValue.YES)
        self.assertEqual(requests[0].headers["authorization"], "Bearer fixture-secret")
        self.assertEqual(
            json.loads(requests[0].content),
            {
                "namespace": "jav",
                "key": "ABC-001",
                "raw_key": "ABC-1",
                "title": "",
            },
        )

    def test_javdb_detail_parses_magnets_sizes_and_attribute_evidence(self):
        http = make_http(
            {
                ("GET", "/v/fixture-one"): (
                    200,
                    fixture_bytes("javdb_detail.html"),
                    "text/html",
                )
            }
        )
        try:
            candidates = JavDbCandidateProvider(http).search(make_item(), run_id="run-1")
            self.assertEqual([candidate.btih[0] for candidate in candidates], ["a", "b"])
            self.assertEqual(candidates[0].size_mb, 1536)
            self.assertEqual(candidates[0].chinese_subtitles, TruthValue.YES)
            self.assertEqual(candidates[0].uhd, TruthValue.YES)
        finally:
            http.close()

    def test_javdb_searches_by_identifier_when_detail_path_is_missing(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/search":
                return httpx.Response(
                    200,
                    content=fixture_bytes("javdb_ranking.html"),
                    headers={"Content-Type": "text/html"},
                    request=request,
                )
            return httpx.Response(
                200,
                content=fixture_bytes("javdb_detail.html"),
                headers={"Content-Type": "text/html"},
                request=request,
            )

        http = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        item = make_item(detail_path=None)
        item.metadata.clear()
        try:
            candidates = JavDbCandidateProvider(
                http,
                cookie="fixture_session=member",
                user_agent="Fixture Browser/1.0",
            ).search(item, run_id="run-search")
        finally:
            http.close()

        self.assertEqual(len(candidates), 2)
        self.assertEqual([request.url.path for request in requests], ["/search", "/v/fixture-one"])
        self.assertEqual(requests[0].url.params["q"], "ABC-001")
        self.assertEqual(requests[0].url.params["f"], "all")
        self.assertEqual(requests[0].headers["cookie"], "fixture_session=member")

    def test_javdb_search_returns_valid_empty_when_identifier_does_not_match(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                content=fixture_bytes("javdb_ranking.html"),
                headers={"Content-Type": "text/html"},
                request=request,
            )

        http = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        item = make_item(raw_key="NOPE-9", normalized_key="NOPE-009", detail_path=None)
        item.metadata.clear()
        try:
            candidates = JavDbCandidateProvider(http).search(item, run_id="run-empty")
        finally:
            http.close()

        self.assertEqual(candidates, [])
        self.assertEqual([request.url.path for request in requests], ["/search"])

    def test_javdb_login_page_has_explicit_authentication_error(self):
        http = make_http(
            {
                ("GET", "/search"): (
                    200,
                    b'<html><form action="/user_sessions"><input name="email"></form></html>',
                    "text/html",
                )
            }
        )
        item = make_item(detail_path=None)
        item.metadata.clear()
        try:
            with self.assertRaisesRegex(ProviderParseError, "authentication_required"):
                JavDbCandidateProvider(http).search(item, run_id="run-login")
        finally:
            http.close()

    def test_sukebei_parses_zero_seeders_as_known_zero(self):
        http = make_http(
            {
                ("GET", "/"): (200, fixture_bytes("sukebei_feed.xml"), "application/rss+xml")
            }
        )
        try:
            candidates = SukebeiCandidateProvider(http).search(make_item(), run_id="run-1")
            self.assertEqual([candidate.seeders for candidate in candidates], [12, 0])
            self.assertEqual([candidate.size_mb for candidate in candidates], [2048, 800])
            self.assertEqual(candidates[0].chinese_subtitles, TruthValue.YES)
        finally:
            http.close()

    def test_knaben_uses_hash_metadata_and_binary_megabytes(self):
        http = make_http(
            {
                ("POST", "/v1"): (
                    200,
                    fixture_bytes("knaben_response.json"),
                    "application/json",
                )
            }
        )
        try:
            candidates = KnabenCandidateProvider(http).search(make_item(), run_id="run-1")
            self.assertEqual([candidate.btih[0] for candidate in candidates], ["e", "f"])
            self.assertEqual(candidates[0].size_mb, 3000)
            self.assertEqual(candidates[0].seeders, 9)
            self.assertEqual(candidates[0].uncensored, TruthValue.YES)
            self.assertEqual(candidates[0].uhd, TruthValue.YES)
        finally:
            http.close()

    def test_bitsearch_uses_infohash_and_treats_coverage_as_optional(self):
        http = make_http(
            {
                ("GET", "/api/v1/search"): (
                    200,
                    fixture_bytes("bitsearch_response.json"),
                    "application/json",
                )
            }
        )
        try:
            candidates = BitsearchCandidateProvider(http).search(make_item(), run_id="run-1")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].btih, "1" * 40)
            self.assertEqual(candidates[0].size_mb, 1024)
            self.assertEqual(candidates[0].seeders, 4)
            self.assertEqual(candidates[0].chinese_subtitles, TruthValue.YES)
        finally:
            http.close()

    def test_valid_empty_payloads_are_not_parse_failures(self):
        http = make_http(
            {
                ("GET", "/"): (200, b"<rss><channel></channel></rss>", "application/rss+xml"),
                ("POST", "/v1"): (200, json.dumps({"hits": []}).encode(), "application/json"),
                ("GET", "/api/v1/search"): (
                    200,
                    json.dumps({"success": True, "results": []}).encode(),
                    "application/json",
                ),
            }
        )
        try:
            self.assertEqual(SukebeiCandidateProvider(http).search(make_item(), run_id="one"), [])
            self.assertEqual(KnabenCandidateProvider(http).search(make_item(), run_id="one"), [])
            self.assertEqual(BitsearchCandidateProvider(http).search(make_item(), run_id="one"), [])
        finally:
            http.close()

    def test_structure_changes_fail_explicitly(self):
        http = make_http(
            {
                ("GET", "/v/fixture-one"): (200, b"<html><body>changed</body></html>", "text/html"),
                ("POST", "/v1"): (200, b'{"unexpected": []}', "application/json"),
                ("GET", "/"): (200, b"<rss><unexpected /></rss>", "application/rss+xml"),
                ("GET", "/api/v1/search"): (
                    200,
                    b'{"success": false, "results": []}',
                    "application/json",
                ),
            }
        )
        try:
            with self.assertRaises(ProviderParseError):
                JavDbCandidateProvider(http).search(make_item(), run_id="one")
            with self.assertRaises(ProviderParseError):
                KnabenCandidateProvider(http).search(make_item(), run_id="one")
            with self.assertRaises(ProviderParseError):
                SukebeiCandidateProvider(http).search(make_item(), run_id="one")
            with self.assertRaises(ProviderParseError):
                BitsearchCandidateProvider(http).search(make_item(), run_id="one")
        finally:
            http.close()


if __name__ == "__main__":
    unittest.main()
