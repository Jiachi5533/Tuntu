from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ProviderError


class ProviderHttpError(ProviderError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    status_code: int
    content: bytes
    content_type: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


class ProviderHttpClient:
    TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        client: httpx.Client,
        timeout_seconds: float = 15,
        retries: int = 2,
        backoff_seconds: float = 0.25,
        cache_ttl_seconds: float = 300,
        min_interval_seconds: float = 0,
        max_response_bytes: int = 3_000_000,
        outbound_proxy: str | None = None,
        clock=time.monotonic,
        sleeper=time.sleep,
    ):
        if retries < 0:
            raise ValueError("retries cannot be negative")
        if min(timeout_seconds, backoff_seconds, cache_ttl_seconds, min_interval_seconds) < 0:
            raise ValueError("HTTP timing values cannot be negative")
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.min_interval_seconds = min_interval_seconds
        self.max_response_bytes = max_response_bytes
        self.outbound_proxy = outbound_proxy
        self._clock = clock
        self._sleep = sleeper
        self._last_request_at: float | None = None
        self._run_cache: dict[tuple[str, tuple], ProviderResponse] = {}
        self._ttl_cache: dict[tuple, tuple[float, ProviderResponse]] = {}
        self._lock = threading.RLock()

    @classmethod
    def build(
        cls,
        *,
        outbound_proxy: str | None = None,
        user_agent: str = "Tuntu/0.1",
        min_interval_seconds: float = 1,
        **options,
    ) -> ProviderHttpClient:
        client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            follow_redirects=True,
            proxy=outbound_proxy,
        )
        return cls(
            client=client,
            outbound_proxy=outbound_proxy,
            min_interval_seconds=min_interval_seconds,
            **options,
        )

    def get(
        self,
        url: str,
        *,
        run_id: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ProviderResponse:
        return self.request(
            "GET", url, run_id=run_id, params=params, headers=headers
        )

    def post(
        self,
        url: str,
        *,
        run_id: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ProviderResponse:
        return self.request(
            "POST", url, run_id=run_id, json_body=json_body, headers=headers
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        run_id: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ProviderResponse:
        with self._lock:
            key = self._cache_key(method, url, params, json_body)
            run_key = (run_id, key)
            if run_key in self._run_cache:
                return self._run_cache[run_key]

            now = self._clock()
            self._ttl_cache = {
                cache_key: value
                for cache_key, value in self._ttl_cache.items()
                if value[0] > now
            }
            cached = self._ttl_cache.get(key)
            if cached is not None and cached[0] > now:
                self._run_cache[run_key] = cached[1]
                return cached[1]

            response = self._request_with_retry(
                method, url, params=params, json_body=json_body, headers=headers
            )
            self._run_cache[run_key] = response
            if self.cache_ttl_seconds > 0:
                self._ttl_cache[key] = (
                    self._clock() + self.cache_ttl_seconds,
                    response,
                )
            return response

    def finish_run(self, run_id: str) -> None:
        with self._lock:
            self._run_cache = {
                key: response
                for key, response in self._run_cache.items()
                if key[0] != run_id
            }

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _cache_key(
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> tuple:
        return (
            method.upper(),
            url,
            json.dumps(params or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            json.dumps(json_body or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def _rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        wait = self.min_interval_seconds - (self._clock() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> ProviderResponse:
        for attempt in range(self.retries + 1):
            self._rate_limit()
            self._last_request_at = self._clock()
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            except httpx.RequestError as exc:
                error_code = (
                    "network_timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"
                )
                if attempt >= self.retries:
                    raise ProviderHttpError(error_code) from exc
                self._sleep(self.backoff_seconds * (2**attempt))
                continue

            if len(response.content) > self.max_response_bytes:
                raise ProviderHttpError("response_too_large")
            if response.status_code in self.TRANSIENT_STATUSES and attempt < self.retries:
                self._sleep(self.backoff_seconds * (2**attempt))
                continue
            if response.status_code >= 400:
                code = (
                    "rate_limited"
                    if response.status_code == 429
                    else "upstream_unavailable"
                    if response.status_code >= 500
                    else f"http_{response.status_code}"
                )
                raise ProviderHttpError(code)
            return ProviderResponse(
                status_code=response.status_code,
                content=response.content,
                content_type=response.headers.get("Content-Type", "").split(";", 1)[0],
            )
        raise AssertionError("retry loop terminated unexpectedly")
