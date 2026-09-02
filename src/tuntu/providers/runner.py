from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .errors import ProviderError


class HealthStore(Protocol):
    def record_source_success(
        self,
        kind: str,
        name: str,
        latency_ms: int,
        result_count: int,
        checked_at: datetime | None = None,
    ) -> None: ...

    def record_source_failure(
        self,
        kind: str,
        name: str,
        error_code: str,
        error_summary: str,
        latency_ms: int,
        checked_at: datetime | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    source: str
    kind: str
    error_code: str


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    source: str
    kind: str
    status: str
    latency_ms: int
    result_count: int
    error_code: str | None = None


@dataclass(slots=True)
class ProviderBatch:
    values: list[Any] = field(default_factory=list)
    failures: list[ProviderFailure] = field(default_factory=list)
    outcomes: list[ProviderOutcome] = field(default_factory=list)


class ProviderRunner:
    def __init__(self, *, health_store: HealthStore, clock=time.perf_counter):
        self.health_store = health_store
        self._clock = clock

    def collect(self, providers, scope: str, *, run_id: str) -> ProviderBatch:
        return self._run(
            providers,
            lambda provider: provider.collect(scope, run_id=run_id),
        )

    def search(self, providers, item, *, run_id: str) -> ProviderBatch:
        return self._run(
            providers,
            lambda provider: provider.search(item, run_id=run_id),
        )

    def _run(self, providers, operation) -> ProviderBatch:
        batch = ProviderBatch()
        for provider in providers:
            started = self._clock()
            try:
                values = operation(provider)
            except ProviderError as exc:
                elapsed = max(0, round((self._clock() - started) * 1_000))
                self.health_store.record_source_failure(
                    provider.kind,
                    provider.name,
                    exc.code,
                    exc.code,
                    elapsed,
                    checked_at=datetime.now(UTC),
                )
                batch.failures.append(
                    ProviderFailure(provider.name, provider.kind, exc.code)
                )
                batch.outcomes.append(
                    ProviderOutcome(
                        provider.name,
                        provider.kind,
                        "failed",
                        elapsed,
                        0,
                        exc.code,
                    )
                )
                continue
            except Exception:
                elapsed = max(0, round((self._clock() - started) * 1_000))
                error_code = "provider_unexpected_error"
                self.health_store.record_source_failure(
                    provider.kind,
                    provider.name,
                    error_code,
                    error_code,
                    elapsed,
                    checked_at=datetime.now(UTC),
                )
                batch.failures.append(
                    ProviderFailure(provider.name, provider.kind, error_code)
                )
                batch.outcomes.append(
                    ProviderOutcome(
                        provider.name,
                        provider.kind,
                        "failed",
                        elapsed,
                        0,
                        error_code,
                    )
                )
                continue
            elapsed = max(0, round((self._clock() - started) * 1_000))
            self.health_store.record_source_success(
                provider.kind,
                provider.name,
                elapsed,
                len(values),
                checked_at=datetime.now(UTC),
            )
            batch.values.extend(values)
            batch.outcomes.append(
                ProviderOutcome(
                    provider.name,
                    provider.kind,
                    "success",
                    elapsed,
                    len(values),
                )
            )
        return batch
