from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from tuntu.db import Database, Repository
from tuntu.db.migration import migrate_database
from tuntu.downloaders.clouddrive import (
    CloudDriveConfigurationError,
    CloudDriveRejected,
    DirectorySnapshot,
    ExternalTaskAlreadyExists,
    ResultUnknown,
    SubmitResult,
)
from tuntu.downloads.service import DownloadService
from tuntu.models import CandidateEvidence, ContentItem, RankingEvidence
from tuntu.normalization import candidate_from_magnet
from tuntu.providers.errors import ProviderError
from tuntu.providers.runner import ProviderRunner
from tuntu.runs.service import RunService


def item(
    key: str,
    *,
    rank: int = 1,
    source: str = "ranking",
    metadata: dict | None = None,
) -> ContentItem:
    return ContentItem(
        namespace="test",
        raw_key=key.upper(),
        normalized_key=key,
        rankings=[
            RankingEvidence(
                source=source,
                rank=rank,
                raw_key=key.upper(),
                scope="weekly",
            )
        ],
        title=key.upper(),
        metadata=metadata or {},
    )


def candidate(content: ContentItem, digit: str, *, seeders: int):
    return candidate_from_magnet(
        item_identity=content.identity,
        evidence=CandidateEvidence(
            source="candidate",
            magnet_uri="magnet:?xt=urn:btih:" + digit * 40,
            title=f"Fixture {digit}",
            seeders=seeders,
        ),
    )


class DiscoveryProvider:
    kind = "discovery"

    def __init__(self, name, values=None, error=None):
        self.name = name
        self.values = list(values or [])
        self.error = error
        self.calls = 0

    def collect(self, scope, *, run_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.values


class CandidateProvider:
    kind = "candidate"

    def __init__(self, name, values=None, error=None):
        self.name = name
        self.values = values or {}
        self.error = error
        self.calls = []

    def search(self, content, *, run_id):
        self.calls.append(content.normalized_key)
        if self.error:
            raise self.error
        return list(self.values.get(content.normalized_key, []))


class FakeCloudDriveClient:
    def __init__(self):
        self.config = SimpleNamespace(
            root_path="/downloads",
            attention_after_seconds=86_400,
            required_stable_observations=2,
        )
        self.submissions = []
        self.rejections = set()
        self.errors = {}

    def ensure_destination(self, destination):
        return None

    def submit(self, magnet_uri, destination):
        btih = magnet_uri.split("urn:btih:", 1)[1]
        self.submissions.append((btih, destination))
        if btih in self.rejections:
            raise CloudDriveRejected("explicit_rejection")
        if btih in self.errors:
            raise self.errors[btih]
        return SubmitResult(btih, destination, DirectorySnapshot(()))


class RunServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "tuntu.db"
        migrate_database(self.db_path, root / "backups")
        self.database = Database(self.db_path)
        self.repo = Repository(self.database)
        self.client = FakeCloudDriveClient()
        self.downloads = DownloadService(self.repo, self.client)

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()

    def profile(self, *, auto_submit=False, top_n=20):
        return self.repo.create_profile(
            "Weekly",
            {
                "scope": "weekly",
                "discovery_sources": ["ranking"],
                "candidate_sources": ["candidate"],
                "rules": {},
                "auto_submit": auto_submit,
            },
            destination_subdir="weekly",
            top_n=top_n,
            daily_time="09:30",
        )

    def service(self, discoveries, candidates):
        return RunService(
            repository=self.repo,
            provider_runner=ProviderRunner(health_store=self.repo),
            discovery_providers=discoveries,
            candidate_providers=candidates,
            download_service=self.downloads,
        )

    def test_dry_run_persists_snapshot_evidence_and_never_calls_cd2(self):
        content = item(
            "item-1", metadata={"cover_url": "https://images.example/item.webp"}
        )
        ranking = DiscoveryProvider("ranking", [content])
        magnets = CandidateProvider(
            "candidate",
            {"item-1": [candidate(content, "a", seeders=1), candidate(content, "b", seeders=9)]},
        )
        profile_id = self.profile(auto_submit=True)

        result = self.service([ranking], [magnets]).execute(
            profile_id, trigger="manual", force_dry_run=True
        )

        run = self.repo.get_run(result.run_id)
        self.assertEqual(result.status, "success")
        self.assertFalse(run.config_snapshot["effective_auto_submit"])
        self.assertEqual(run.config_snapshot["destination"], "/downloads/weekly")
        self.assertEqual(run.stats["items_selected"], 1)
        self.assertEqual(run.stats["submit_attempts"], 0)
        self.assertEqual(self.client.submissions, [])
        self.assertEqual(self.repo.count_run_items(result.run_id), 1)
        self.assertEqual(self.repo.count_evaluations(result.run_id), 2)
        self.assertEqual(self.repo.count_candidate_evidence(result.run_id), 2)
        self.assertEqual(
            self.repo.get_run_detail(result.run_id)["items"][0]["metadata"][
                "cover_url"
            ],
            "https://images.example/item.webp",
        )

    def test_manual_number_run_is_persisted_and_forces_dry_run(self):
        manual_content = ContentItem(
            namespace="jav",
            raw_key="abc-123",
            normalized_key="ABC-123",
            rankings=[
                RankingEvidence(
                    source="manual", rank=1, raw_key="abc-123", scope="manual"
                )
            ],
            title="abc-123",
        )
        magnets = CandidateProvider(
            "candidate",
            {"ABC-123": [candidate(manual_content, "a", seeders=8)]},
        )

        result = self.service([], [magnets]).execute(
            self.profile(auto_submit=True),
            trigger="manual_number",
            manual_raw_keys=["abc-123"],
        )

        detail = self.repo.get_run_detail(result.run_id)
        self.assertEqual(result.status, "success")
        self.assertEqual(detail["trigger"], "manual_number")
        self.assertEqual(detail["config_snapshot"]["discovery_sources"], ["manual"])
        self.assertEqual(detail["config_snapshot"]["manual_raw_keys"], ["abc-123"])
        self.assertFalse(detail["config_snapshot"]["effective_auto_submit"])
        self.assertEqual(detail["items"][0]["normalized_key"], "abc-123")
        self.assertTrue(detail["items"][0]["evaluations"][0]["accepted"])
        self.assertEqual(self.client.submissions, [])

    def test_manual_keys_and_trigger_must_be_used_together(self):
        service = self.service([], [CandidateProvider("candidate")])
        profile_id = self.profile()

        with self.assertRaisesRegex(ValueError, "manual_number"):
            service.execute(
                profile_id,
                trigger="manual",
                manual_raw_keys=["ABC-123"],
            )
        with self.assertRaisesRegex(ValueError, "manual_number"):
            service.execute(profile_id, trigger="manual_number")

    def test_watchlist_run_uses_manual_keys_but_honors_its_auto_submit_switch(self):
        watchlist_content = ContentItem(
            namespace="jav",
            raw_key="abc-123",
            normalized_key="ABC-123",
            rankings=[
                RankingEvidence(
                    source="manual", rank=1, raw_key="abc-123", scope="watchlist"
                )
            ],
            title="abc-123",
        )
        magnets = CandidateProvider(
            "candidate",
            {"ABC-123": [candidate(watchlist_content, "a", seeders=8)]},
        )

        result = self.service([], [magnets]).execute(
            self.profile(auto_submit=False),
            trigger="watchlist",
            manual_raw_keys=["abc-123"],
            auto_submit_override=True,
        )

        detail = self.repo.get_run_detail(result.run_id)
        self.assertEqual(result.status, "success")
        self.assertEqual(detail["trigger"], "watchlist")
        self.assertEqual(detail["config_snapshot"]["scope"], "watchlist")
        self.assertTrue(detail["config_snapshot"]["effective_auto_submit"])
        self.assertEqual(
            self.client.submissions,
            [("a" * 40, "/downloads/weekly/" + "a" * 40)],
        )

    def test_auto_mode_submits_only_best_candidate_for_content(self):
        content = item("item-1")
        ranking = DiscoveryProvider("ranking", [content])
        magnets = CandidateProvider(
            "candidate",
            {"item-1": [candidate(content, "a", seeders=1), candidate(content, "b", seeders=9)]},
        )

        result = self.service([ranking], [magnets]).execute(
            self.profile(auto_submit=True), trigger="scheduled"
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            self.client.submissions,
            [("b" * 40, "/downloads/weekly/" + "b" * 40)],
        )
        self.assertEqual(self.repo.get_run(result.run_id).stats["items_submitted"], 1)

    def test_auto_mode_submits_one_best_candidate_for_each_top_n_content(self):
        first = item("item-1", rank=1)
        second = item("item-2", rank=2)
        ranking = DiscoveryProvider("ranking", [first, second])
        magnets = CandidateProvider(
            "candidate",
            {
                "item-1": [candidate(first, "a", seeders=5)],
                "item-2": [candidate(second, "b", seeders=6)],
            },
        )

        result = self.service([ranking], [magnets]).execute(
            self.profile(auto_submit=True, top_n=2), trigger="scheduled"
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(len(self.client.submissions), 2)
        self.assertEqual(self.repo.get_run(result.run_id).stats["items_submitted"], 2)

    def test_explicit_rejection_falls_back_but_other_client_error_does_not(self):
        content = item("item-1")
        ranking = DiscoveryProvider("ranking", [content])
        magnets = CandidateProvider(
            "candidate",
            {"item-1": [candidate(content, "a", seeders=9), candidate(content, "b", seeders=1)]},
        )
        self.client.rejections.add("a" * 40)

        fallback = self.service([ranking], [magnets]).execute(
            self.profile(auto_submit=True), trigger="manual"
        )

        self.assertEqual(fallback.status, "partial")
        self.assertEqual(
            self.client.submissions,
            [
                ("a" * 40, "/downloads/weekly/" + "a" * 40),
                ("b" * 40, "/downloads/weekly/" + "b" * 40),
            ],
        )
        self.assertEqual(self.repo.get_run(fallback.run_id).stats["candidate_fallbacks"], 1)

        second_content = item("item-2")
        second_ranking = DiscoveryProvider("ranking", [second_content])
        second_magnets = CandidateProvider(
            "candidate",
            {
                "item-2": [
                    candidate(second_content, "c", seeders=9),
                    candidate(second_content, "d", seeders=1),
                ]
            },
        )
        self.client.errors["c" * 40] = CloudDriveConfigurationError("invalid_remote_path")

        no_fallback = self.service([second_ranking], [second_magnets]).execute(
            self.profile(auto_submit=True), trigger="manual"
        )

        self.assertEqual(no_fallback.status, "partial")
        self.assertNotIn(
            ("d" * 40, "/downloads/weekly/" + "d" * 40),
            self.client.submissions,
        )

    def test_unknown_or_external_duplicate_submission_is_partial_and_never_falls_back(self):
        cases = (
            (ResultUnknown("submission_result_unknown"), "submit_unknown"),
            (
                ExternalTaskAlreadyExists("external_task_already_exists"),
                "submit_attention_required",
            ),
        )
        for index, (error, expected_stat) in enumerate(cases, 1):
            with self.subTest(expected_stat=expected_stat):
                content = item(f"item-{index + 10}")
                first_digit = str(index)
                second_digit = str(index + 5)
                ranking = DiscoveryProvider("ranking", [content])
                magnets = CandidateProvider(
                    "candidate",
                    {
                        content.normalized_key: [
                            candidate(content, first_digit, seeders=9),
                            candidate(content, second_digit, seeders=1),
                        ]
                    },
                )
                self.client.errors[first_digit * 40] = error

                result = self.service([ranking], [magnets]).execute(
                    self.profile(auto_submit=True), trigger="manual"
                )

                self.assertEqual(result.status, "partial")
                stats = self.repo.get_run(result.run_id).stats
                self.assertEqual(stats[expected_stat], 1)
                self.assertNotIn(
                    second_digit * 40,
                    [btih for btih, _ in self.client.submissions],
                )

    def test_partial_sources_keep_results_but_all_discovery_failures_fail_run(self):
        content = item("item-1")
        ranking = DiscoveryProvider("ranking", [content])
        broken_ranking = DiscoveryProvider(
            "broken-ranking", error=ProviderError("network_timeout")
        )
        magnets = CandidateProvider(
            "candidate", {"item-1": [candidate(content, "a", seeders=1)]}
        )
        broken_magnets = CandidateProvider(
            "broken-candidate", error=ProviderError("upstream_unavailable")
        )
        profile_id = self.repo.create_profile(
            "Partial",
            {
                "scope": "weekly",
                "discovery_sources": ["ranking", "broken-ranking"],
                "candidate_sources": ["candidate", "broken-candidate"],
                "rules": {},
            },
            destination_subdir="partial",
        )

        partial = self.service(
            [ranking, broken_ranking], [magnets, broken_magnets]
        ).execute(profile_id, trigger="manual")

        self.assertEqual(partial.status, "partial")
        source_results = self.repo.list_run_source_results(partial.run_id)
        self.assertEqual(
            {(row.source_name, row.status) for row in source_results},
            {
                ("ranking", "success"),
                ("broken-ranking", "failed"),
                ("candidate", "success"),
                ("broken-candidate", "failed"),
            },
        )

        failed_profile = self.repo.create_profile(
            "Failed",
            {
                "scope": "weekly",
                "discovery_sources": ["broken-ranking"],
                "candidate_sources": ["candidate"],
            },
            destination_subdir="failed",
        )
        failed = self.service([broken_ranking], [magnets]).execute(
            failed_profile, trigger="scheduled"
        )

        self.assertEqual(failed.status, "failed")
        self.assertEqual(magnets.calls, ["item-1"])

    def test_disabled_selected_sources_are_skipped_when_alternatives_remain(self):
        content = item("item-enabled")
        ranking = DiscoveryProvider("ranking", [content])
        magnets = CandidateProvider(
            "candidate",
            {"item-enabled": [candidate(content, "e", seeders=5)]},
        )
        profile_id = self.repo.create_profile(
            "Globally disabled alternatives",
            {
                "scope": "weekly",
                "discovery_sources": ["ranking", "disabled-ranking"],
                "candidate_sources": ["candidate", "disabled-candidate"],
                "rules": {},
            },
            destination_subdir="degraded",
        )

        result = self.service([ranking], [magnets]).execute(
            profile_id, trigger="manual"
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(self.repo.get_run(result.run_id).stats["items_selected"], 1)

    def test_empty_success_and_cross_profile_deduplication_are_normal_results(self):
        empty = DiscoveryProvider("ranking", [])
        magnets = CandidateProvider("candidate")
        empty_result = self.service([empty], [magnets]).execute(
            self.profile(), trigger="scheduled"
        )
        self.assertEqual(empty_result.status, "success")

        content = item("item-9")
        ranking = DiscoveryProvider("ranking", [content])
        candidate_provider = CandidateProvider(
            "candidate", {"item-9": [candidate(content, "9", seeders=9)]}
        )
        service = self.service([ranking], [candidate_provider])
        first = service.execute(self.profile(auto_submit=True), trigger="manual")
        second_profile = self.repo.create_profile(
            "Other",
            {
                "scope": "weekly",
                "discovery_sources": ["ranking"],
                "candidate_sources": ["candidate"],
                "auto_submit": True,
            },
            destination_subdir="other",
        )
        second = service.execute(second_profile, trigger="manual")

        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "success")
        self.assertEqual(self.repo.get_run(second.run_id).stats["items_deduplicated"], 1)
        duplicate = self.repo.get_run_detail(second.run_id)["items"][0]["duplicate"]
        self.assertEqual(duplicate["profile_id"], self.repo.get_run(first.run_id).profile_id)
        self.assertEqual(duplicate["status"], "submitted")
        self.assertIsNotNone(duplicate["task_id"])
        self.assertEqual(len(self.client.submissions), 1)

    def test_same_profile_overlap_is_skipped_and_different_profiles_can_run_in_parallel(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingDiscovery(DiscoveryProvider):
            def collect(self, scope, *, run_id):
                entered.set()
                if not release.wait(timeout=3):
                    raise RuntimeError("fixture wait timeout")
                return []

        blocking = BlockingDiscovery("ranking")
        service = self.service([blocking], [CandidateProvider("candidate")])
        profile_id = self.profile()
        first_results = []
        first = threading.Thread(
            target=lambda: first_results.append(
                service.execute(profile_id, trigger="scheduled")
            )
        )
        first.start()
        self.assertTrue(entered.wait(timeout=2))

        overlap = service.execute(profile_id, trigger="scheduled")
        release.set()
        first.join(timeout=3)

        self.assertEqual(overlap.status, "skipped")
        self.assertEqual(overlap.skipped_reason, "profile_overlap")
        self.assertEqual(first_results[0].status, "success")
        self.assertEqual(self.repo.count_audit_events("run_skipped"), 1)

        barrier = threading.Barrier(2, timeout=3)

        class ParallelDiscovery(DiscoveryProvider):
            def collect(self, scope, *, run_id):
                barrier.wait()
                return []

        parallel_service = RunService(
            repository=self.repo,
            provider_runner=ProviderRunner(health_store=self.repo),
            discovery_providers=[ParallelDiscovery("ranking")],
            candidate_providers=[CandidateProvider("candidate")],
            download_service=self.downloads,
            max_concurrent_runs=2,
        )
        profile_ids = [self.profile(), self.profile()]
        parallel_results = []
        threads = [
            threading.Thread(
                target=lambda current=profile: parallel_results.append(
                    parallel_service.execute(current, trigger="scheduled")
                )
            )
            for profile in profile_ids
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=4)

        self.assertEqual(
            sorted(result.status for result in parallel_results),
            ["success", "success"],
        )

    def test_stale_scheduled_trigger_cannot_run_a_disabled_profile(self):
        profile_id = self.profile()
        self.repo.set_profile_enabled(profile_id, False)
        ranking = DiscoveryProvider("ranking", [])

        result = self.service(
            [ranking], [CandidateProvider("candidate")]
        ).execute(profile_id, trigger="scheduled")

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.skipped_reason, "profile_disabled")
        self.assertEqual(ranking.calls, 0)


if __name__ == "__main__":
    unittest.main()
