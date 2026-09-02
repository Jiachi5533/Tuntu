from __future__ import annotations

import threading
import uuid

from tuntu.downloaders.clouddrive import CloudDriveClient
from tuntu.downloads.poller import DownloadPoller
from tuntu.downloads.service import DownloadService
from tuntu.profiles import CANDIDATE_SOURCES, DISCOVERY_SOURCES
from tuntu.providers import (
    AuthorizedJsonCandidateProvider,
    BitsearchCandidateProvider,
    JavDatabaseRankingProvider,
    JavDbCandidateProvider,
    JavDbRankingProvider,
    KnabenCandidateProvider,
    SukebeiCandidateProvider,
)
from tuntu.providers.http import ProviderHttpClient
from tuntu.providers.runner import ProviderRunner
from tuntu.runs.service import RunService
from tuntu.scheduler import TuntuScheduler
from tuntu.settings import SettingsError


class RuntimeUnavailable(RuntimeError):
    def __init__(self, code: str = "runtime_unavailable"):
        self.code = code
        super().__init__(code)


class RuntimeManager:
    def __init__(self, repository, settings_service, *, start_scheduler: bool = True):
        self.repository = repository
        self.settings_service = settings_service
        self.start_scheduler = start_scheduler
        self._lock = threading.RLock()
        self.http = None
        self.client = None
        self.download_service = None
        self.run_service = None
        self.poller = None
        self.scheduler = None
        self.discovery_providers = {}
        self.candidate_providers = {}
        self.watchlist_runner = None
        self.error_code = "cd2_not_configured"

    @property
    def configured(self) -> bool:
        return self.run_service is not None

    def reload(self) -> bool:
        with self._lock:
            self.close()
            try:
                values = self.settings_service.get_effective()
                config = self.settings_service.build_clouddrive_config()
            except SettingsError as exc:
                self.error_code = exc.code
                return False
            http = ProviderHttpClient.build(
                outbound_proxy=values["outbound_proxy"],
                timeout_seconds=values["provider_timeout_seconds"],
                retries=values["provider_retries"],
                backoff_seconds=values["provider_backoff_seconds"],
                cache_ttl_seconds=values["provider_cache_ttl_seconds"],
                min_interval_seconds=values["provider_min_interval_seconds"],
                max_response_bytes=values["provider_max_response_bytes"],
            )
            client = CloudDriveClient(config)
            discovery = [
                JavDbRankingProvider(
                    http,
                    base_url=values["javdb_base_url"],
                    cookie=values["javdb_cookie"],
                    user_agent=values["javdb_user_agent"],
                ),
                JavDatabaseRankingProvider(
                    http, feed_url=values["javdatabase_feed_url"]
                ),
            ]
            candidates = [
                JavDbCandidateProvider(
                    http,
                    base_url=values["javdb_base_url"],
                    cookie=values["javdb_cookie"],
                    user_agent=values["javdb_user_agent"],
                ),
                SukebeiCandidateProvider(
                    http, feed_url=values["sukebei_feed_url"]
                ),
                KnabenCandidateProvider(
                    http, endpoint=values["knaben_api_url"]
                ),
                BitsearchCandidateProvider(
                    http, endpoint=values["bitsearch_api_url"]
                ),
            ]
            if values["authorized_candidate_api_url"]:
                candidates.insert(
                    0,
                    AuthorizedJsonCandidateProvider(
                        http,
                        endpoint=values["authorized_candidate_api_url"],
                        api_token=values["authorized_candidate_api_token"],
                    ),
                )
            disabled_sources = set(values["disabled_sources"])
            runner = ProviderRunner(health_store=self.repository)
            downloads = DownloadService(self.repository, client)
            run_service = RunService(
                repository=self.repository,
                provider_runner=runner,
                discovery_providers=[
                    provider
                    for provider in discovery
                    if provider.name not in disabled_sources
                ],
                candidate_providers=[
                    provider
                    for provider in candidates
                    if provider.name not in disabled_sources
                ],
                download_service=downloads,
                max_concurrent_runs=values["max_concurrent_runs"],
            )
            poller = DownloadPoller(self.repository, downloads)
            scheduler = TuntuScheduler(
                repository=self.repository,
                run_service=run_service,
                download_poller=poller,
                watchlist_runner=self.watchlist_runner,
                timezone_name=values["timezone"],
                poll_interval_seconds=config.poll_interval_seconds,
                max_workers=values["max_concurrent_runs"],
            )
            self.http = http
            self.client = client
            self.download_service = downloads
            self.run_service = run_service
            self.poller = poller
            self.scheduler = scheduler
            self.discovery_providers = {provider.name: provider for provider in discovery}
            self.candidate_providers = {provider.name: provider for provider in candidates}
            self.error_code = None
            if self.start_scheduler:
                scheduler.start()
            return True

    def require(self):
        if not self.configured:
            raise RuntimeUnavailable(self.error_code or "runtime_unavailable")
        return self

    def sync_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.sync()

    def execute_profile(self, profile_id: int, *, force_dry_run: bool):
        return self.require().run_service.execute(
            profile_id,
            trigger="manual",
            force_dry_run=force_dry_run,
        )

    def probe_source(self, name: str, *, query: str | None, scope: str) -> dict:
        runtime = self.require()
        run_id = "probe-" + str(uuid.uuid4())
        try:
            runner = ProviderRunner(health_store=self.repository)
            if name in runtime.discovery_providers:
                batch = runner.collect(
                    [runtime.discovery_providers[name]], scope, run_id=run_id
                )
            elif name in runtime.candidate_providers:
                if not query:
                    raise RuntimeUnavailable("source_query_required")
                from tuntu.models import ContentItem, RankingEvidence
                from tuntu.providers.attributes import normalize_jav_identity

                namespace, normalized = normalize_jav_identity(
                    query, fallback_namespace="manual"
                )
                item = ContentItem(
                    namespace,
                    query,
                    normalized,
                    [RankingEvidence("manual", 1, query, scope)],
                    title=query,
                )
                batch = runner.search(
                    [runtime.candidate_providers[name]], item, run_id=run_id
                )
            else:
                raise RuntimeUnavailable("source_not_found")
            outcome = batch.outcomes[0]
            return {
                "name": name,
                "status": outcome.status,
                "latency_ms": outcome.latency_ms,
                "result_count": outcome.result_count,
                "error_code": outcome.error_code,
            }
        finally:
            if self.http is not None:
                self.http.finish_run(run_id)

    def source_catalog(self) -> list[dict]:
        settings = self.settings_service.get_effective()
        disabled_sources = set(settings["disabled_sources"])
        health = {
            (row.source_kind, row.source_name): row
            for row in self.repository.list_source_health()
        }
        entries = []
        for catalog in (DISCOVERY_SOURCES, CANDIDATE_SOURCES):
            for item in catalog.values():
                row = health.get((item["kind"], item["name"]))
                entries.append(
                    {
                        **item,
                        "configured": (
                            item["name"] != "authorized_json_api"
                            or bool(settings["authorized_candidate_api_url"])
                        ),
                        "enabled": item["name"] not in disabled_sources
                        and (
                            item["name"] != "authorized_json_api"
                            or bool(settings["authorized_candidate_api_url"])
                        ),
                        "health": (
                            None
                            if row is None
                            else {
                                "last_success_at": row.last_success_at,
                                "last_checked_at": row.last_checked_at,
                                "last_latency_ms": row.last_latency_ms,
                                "last_result_count": row.last_result_count,
                                "consecutive_failures": row.consecutive_failures,
                                "last_error_code": row.last_error_code,
                            }
                        ),
                    }
                )
        return entries

    def set_source_enabled(self, name: str, enabled: bool) -> dict:
        known = set(DISCOVERY_SOURCES) | set(CANDIDATE_SOURCES)
        if name not in known:
            raise RuntimeUnavailable("source_not_found")
        disabled = set(
            self.settings_service.get_effective()["disabled_sources"]
        )
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        self.settings_service.update({"disabled_sources": sorted(disabled)})
        self.reload()
        return next(
            item for item in self.source_catalog() if item["name"] == name
        )

    def close(self) -> None:
        scheduler, client, http = self.scheduler, self.client, self.http
        self.scheduler = None
        self.client = None
        self.http = None
        self.download_service = None
        self.run_service = None
        self.poller = None
        self.discovery_providers = {}
        self.candidate_providers = {}
        if scheduler is not None:
            scheduler.shutdown(wait=True)
        if client is not None:
            client.close()
        if http is not None:
            http.close()
