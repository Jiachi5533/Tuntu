from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from tuntu.api import build_services, create_app
from tuntu.config import StartupConfig
from tuntu.downloaders.clouddrive import DirectorySnapshot, SubmitResult, TaskSignal
from tuntu.downloads.service import DownloadService
from tuntu.manual import ManualService
from tuntu.models import CandidateEvidence, ContentItem, RankingEvidence, TruthValue
from tuntu.normalization import candidate_from_magnet
from tuntu.profiles import CANDIDATE_SOURCES, DISCOVERY_SOURCES
from tuntu.providers.runner import ProviderRunner
from tuntu.runs.service import RunService


class FixtureCloudDriveClient:
    def __init__(self):
        self.config = SimpleNamespace(
            root_path="/fixture-cloud",
            attention_after_seconds=86_400,
            required_stable_observations=2,
        )

    def ensure_destination(self, _destination):
        return None

    def submit(self, magnet_uri, destination):
        btih = magnet_uri.split("urn:btih:", 1)[1].split("&", 1)[0]
        return SubmitResult(btih, destination, DirectorySnapshot(()))

    def get_task_signal(self, _btih, _destination):
        return TaskSignal.UNKNOWN

    def snapshot(self, _destination, *, force_refresh):
        return DirectorySnapshot(())


class FixtureDiscoveryProvider:
    name = "javdb_ranking"
    kind = "discovery"

    def collect(self, scope, *, run_id):
        return [
            ContentItem(
                namespace="fixture",
                raw_key="DEMO-001",
                normalized_key="demo-001",
                rankings=[
                    RankingEvidence(self.name, 1, "DEMO-001", scope)
                ],
                title="离线验收条目",
            )
        ]


class FixtureCandidateProvider:
    kind = "candidate"

    def __init__(self, name, seeders):
        self.name = name
        self.seeders = seeders

    def search(self, content, *, run_id):
        btih = hashlib.sha1(
            f"{content.namespace}:{content.normalized_key}".encode()
        ).hexdigest()
        return [
            candidate_from_magnet(
                item_identity=content.identity,
                evidence=CandidateEvidence(
                    source=self.name,
                    magnet_uri=(
                        f"magnet:?xt=urn:btih:{btih}&dn=offline-fixture"
                    ),
                    title=f"{content.raw_key} 离线验收 中文字幕",
                    size_mb=256.0,
                    seeders=self.seeders,
                    chinese_subtitles=TruthValue.YES,
                ),
            )
        ]


class FixtureRuntime:
    def __init__(self, repository, settings):
        self.repository = repository
        self.settings = settings
        self.client = FixtureCloudDriveClient()
        self.download_service = DownloadService(repository, self.client)
        discoveries = [FixtureDiscoveryProvider()]
        candidates = [
            FixtureCandidateProvider("javdb_detail", 4),
            FixtureCandidateProvider("knaben_api", 9),
        ]
        self.run_service = RunService(
            repository=repository,
            provider_runner=ProviderRunner(health_store=repository),
            discovery_providers=discoveries,
            candidate_providers=candidates,
            download_service=self.download_service,
        )
        self.error_code = None

    @property
    def configured(self):
        return True

    def require(self):
        return self

    def reload(self):
        return True

    def close(self):
        return None

    def sync_scheduler(self):
        return None

    def execute_profile(self, profile_id, *, force_dry_run):
        return self.run_service.execute(
            profile_id,
            trigger="manual",
            force_dry_run=force_dry_run,
        )

    def source_catalog(self):
        disabled = set(self.settings.get_effective()["disabled_sources"])
        return [
            {**source, "enabled": source["name"] not in disabled, "health": None}
            for catalog in (DISCOVERY_SOURCES, CANDIDATE_SOURCES)
            for source in catalog.values()
        ]

    def set_source_enabled(self, name, enabled):
        known = set(DISCOVERY_SOURCES) | set(CANDIDATE_SOURCES)
        if name not in known:
            raise ValueError("source_not_found")
        disabled = set(self.settings.get_effective()["disabled_sources"])
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        self.settings.update({"disabled_sources": sorted(disabled)})
        return next(item for item in self.source_catalog() if item["name"] == name)

    def probe_source(self, name, *, query, scope):
        return {
            "name": name,
            "status": "success",
            "latency_ms": 1,
            "result_count": 1,
            "error_code": None,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    config = StartupConfig(data_dir=args.data_dir, host="127.0.0.1", port=args.port)
    services = build_services(config, start_scheduler=False)
    services.runtime.close()
    runtime = FixtureRuntime(services.repository, services.settings)
    services.runtime = runtime
    services.manual = ManualService(services.repository, runtime)
    services.profiles._scheduler_sync = runtime.sync_scheduler
    grant = services.auth.rotate_setup_token()
    print(f"E2E_SETUP_TOKEN={grant.token}", flush=True)
    uvicorn.run(create_app(config, services=services), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
