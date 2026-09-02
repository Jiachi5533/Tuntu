from __future__ import annotations

import unittest
import threading

import httpx

from tuntu.providers.http import ProviderHttpClient, ProviderHttpError
from tuntu.providers.runner import ProviderRunner


class FakeClock:
    def __init__(self):
        self.value = 100.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds

    def advance(self, seconds):
        self.value += seconds


class ProviderHttpTests(unittest.TestCase):
    def test_concurrent_runs_share_one_in_flight_cache_boundary(self):
        calls = 0
        barrier = threading.Barrier(2)

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=b"fixture", request=request)

        client = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            cache_ttl_seconds=10,
        )
        results = []

        def request(run_id):
            barrier.wait()
            results.append(
                client.get("https://source.invalid/feed", run_id=run_id).content
            )

        try:
            threads = [
                threading.Thread(target=request, args=("one",)),
                threading.Thread(target=request, args=("two",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            self.assertEqual(results, [b"fixture", b"fixture"])
            self.assertEqual(calls, 1)
        finally:
            client.close()

    def test_same_run_deduplicates_and_ttl_cache_reuses_across_runs(self):
        calls = []
        clock = FakeClock()

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=b"fixture", request=request)

        client = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            cache_ttl_seconds=10,
            clock=clock,
            sleeper=clock.sleep,
        )
        try:
            self.assertEqual(client.get("https://source.invalid/feed", run_id="one").content, b"fixture")
            client.get("https://source.invalid/feed", run_id="one")
            client.get("https://source.invalid/feed", run_id="two")
            self.assertEqual(len(calls), 1)

            clock.advance(11)
            client.get("https://source.invalid/feed", run_id="three")
            self.assertEqual(len(calls), 2)
        finally:
            client.close()

    def test_retry_is_finite_and_uses_exponential_backoff(self):
        attempts = 0
        clock = FakeClock()

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectTimeout("fixture timeout", request=request)
            if attempts == 2:
                return httpx.Response(503, request=request)
            return httpx.Response(200, content=b"ok", request=request)

        client = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            retries=2,
            backoff_seconds=0.1,
            clock=clock,
            sleeper=clock.sleep,
        )
        try:
            self.assertEqual(client.get("https://source.invalid", run_id="one").content, b"ok")
            self.assertEqual(attempts, 3)
            self.assertEqual(clock.sleeps, [0.1, 0.2])
        finally:
            client.close()

    def test_retry_exhaustion_returns_sanitized_error(self):
        def handler(request):
            raise httpx.ReadTimeout("query=secret-value", request=request)

        client = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)), retries=1
        )
        try:
            with self.assertRaisesRegex(ProviderHttpError, "network_timeout") as caught:
                client.get("https://source.invalid?q=secret-value", run_id="one")
            self.assertNotIn("secret-value", str(caught.exception))
        finally:
            client.close()

    def test_rate_limit_applies_between_real_requests_but_not_cache_hits(self):
        clock = FakeClock()
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=str(calls).encode(), request=request)

        client = ProviderHttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            cache_ttl_seconds=0,
            min_interval_seconds=2,
            clock=clock,
            sleeper=clock.sleep,
        )
        try:
            client.get("https://source.invalid/one", run_id="one")
            client.get("https://source.invalid/one", run_id="one")
            client.get("https://source.invalid/two", run_id="one")
            self.assertEqual(calls, 2)
            self.assertEqual(clock.sleeps, [2])
        finally:
            client.close()

    def test_public_provider_proxy_is_explicit_configuration(self):
        client = ProviderHttpClient.build(outbound_proxy="socks5://proxy.invalid:1080")
        try:
            self.assertEqual(client.outbound_proxy, "socks5://proxy.invalid:1080")
        finally:
            client.close()


class RunnerTests(unittest.TestCase):
    class HealthStore:
        def __init__(self):
            self.events = []

        def record_source_success(self, kind, name, latency_ms, result_count, checked_at=None):
            self.events.append(("success", kind, name, result_count))

        def record_source_failure(self, kind, name, error_code, error_summary, latency_ms, checked_at=None):
            self.events.append(("failure", kind, name, error_code))

    class Provider:
        kind = "discovery"

        def __init__(self, name, result=None, error=None):
            self.name = name
            self.result = result if result is not None else []
            self.error = error

        def collect(self, scope, *, run_id):
            if self.error:
                raise self.error
            return self.result

    def test_one_provider_failure_does_not_cancel_other_or_disable_it(self):
        health = self.HealthStore()
        runner = ProviderRunner(health_store=health)
        providers = [
            self.Provider("broken", error=ProviderHttpError("network_timeout")),
            self.Provider("unexpected", error=RuntimeError("private fixture detail")),
            self.Provider("empty", result=[]),
            self.Provider("working", result=["one"]),
        ]

        batch = runner.collect(providers, "weekly", run_id="run-1")

        self.assertEqual(batch.values, ["one"])
        self.assertEqual(
            [failure.source for failure in batch.failures],
            ["broken", "unexpected"],
        )
        self.assertEqual(
            [
                (outcome.source, outcome.status, outcome.result_count, outcome.error_code)
                for outcome in batch.outcomes
            ],
            [
                ("broken", "failed", 0, "network_timeout"),
                ("unexpected", "failed", 0, "provider_unexpected_error"),
                ("empty", "success", 0, None),
                ("working", "success", 1, None),
            ],
        )
        self.assertEqual(
            health.events,
            [
                ("failure", "discovery", "broken", "network_timeout"),
                (
                    "failure",
                    "discovery",
                    "unexpected",
                    "provider_unexpected_error",
                ),
                ("success", "discovery", "empty", 0),
                ("success", "discovery", "working", 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
