from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tuntu.db import Database, Repository
from tuntu.db.migration import migrate_database
from tuntu.downloaders.clouddrive import DirectorySnapshot, SubmitResult
from tuntu.downloads.service import DownloadService
from tuntu.manual import ManualError, ManualService
from tuntu.models import CandidateEvidence
from tuntu.normalization import candidate_from_magnet
from tuntu.providers.runner import ProviderRunner
from tuntu.runs.service import RunService


class FakeCloudDriveClient:
    def __init__(self):
        self.config = SimpleNamespace(
            root_path="/configured-root",
            attention_after_seconds=86_400,
            required_stable_observations=2,
        )
        self.submissions = []

    def ensure_destination(self, destination):
        pass

    def submit(self, magnet_uri, destination):
        btih = magnet_uri.rsplit(":", 1)[1]
        self.submissions.append((btih, destination))
        return SubmitResult(btih, destination, DirectorySnapshot(()))


class FakeRuntime:
    def __init__(self, repository):
        self.client = FakeCloudDriveClient()
        self.download_service = DownloadService(repository, self.client)

    def require(self):
        return self


class FixtureCandidateProvider:
    name = "candidate"
    kind = "candidate"

    def search(self, content, *, run_id):
        return [
            candidate_from_magnet(
                item_identity=content.identity,
                evidence=CandidateEvidence(
                    source=self.name,
                    magnet_uri="magnet:?xt=urn:btih:" + "b" * 40,
                    title=f"{content.raw_key} fixture",
                    seeders=8,
                ),
            )
        ]


class ManualServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        migrate_database(root / "tuntu.db", root / "backups")
        self.database = Database(root / "tuntu.db")
        self.repository = Repository(self.database)
        self.profile_id = self.repository.create_profile(
            "Fixture",
            {
                "discovery_sources": [],
                "candidate_sources": ["candidate"],
                "rules": {},
                "auto_submit": True,
            },
            destination_subdir="Tuntu/manual",
        )
        self.runtime = FakeRuntime(self.repository)
        self.runtime.run_service = RunService(
            repository=self.repository,
            provider_runner=ProviderRunner(health_store=self.repository),
            discovery_providers=[],
            candidate_providers=[FixtureCandidateProvider()],
            download_service=self.runtime.download_service,
        )
        self.service = ManualService(self.repository, self.runtime)
        self.magnet = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=ignored"

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()

    def test_preview_normalizes_btih_destination_and_duplicate(self):
        preview = self.service.magnet_preview(self.profile_id, self.magnet, "测试")
        self.assertEqual(preview["btih"], "a" * 40)
        self.assertEqual(
            preview["destination"],
            "/configured-root/Tuntu/manual/" + "a" * 40,
        )
        self.assertIsNone(preview["duplicate"])

        submitted = self.service.submit_magnet(
            self.profile_id, self.magnet, title="测试", confirmed=True
        )
        self.assertEqual(submitted["status"], "submitted")
        duplicate = self.service.magnet_preview(self.profile_id, self.magnet)
        self.assertTrue(duplicate["requires_force_confirmation"])

    def test_duplicate_requires_explicit_force_and_confirmation(self):
        first = self.service.submit_magnet(
            self.profile_id, self.magnet, confirmed=True
        )
        self.runtime.download_service.manual_complete(first["id"], confirmed=True)
        with self.assertRaisesRegex(ManualError, "force_confirmation_required"):
            self.service.submit_magnet(
                self.profile_id, self.magnet, confirmed=True
            )
        with self.assertRaisesRegex(ManualError, "confirmation_required"):
            self.service.submit_magnet(
                self.profile_id, self.magnet, force=True, confirmed=False
            )

        second = self.service.submit_magnet(
            self.profile_id, self.magnet, force=True, confirmed=True
        )
        self.assertEqual(second["generation"], 1)
        self.assertEqual(second["supersedes_task_id"], first["id"])
        self.assertEqual(
            self.repository.count_audit_events("forced_magnet_submit"), 1
        )

    def test_invalid_v2_only_magnet_and_archived_profile_are_rejected(self):
        with self.assertRaisesRegex(ManualError, "invalid_magnet"):
            self.service.magnet_preview(
                self.profile_id, "magnet:?xt=urn:btmh:1220" + "a" * 64
            )
        self.repository.archive_profile(self.profile_id)
        with self.assertRaisesRegex(ManualError, "profile_archived"):
            self.service.magnet_preview(self.profile_id, self.magnet)

    def test_direct_magnet_requires_confirmation_and_rejects_unneeded_force(self):
        with self.assertRaisesRegex(ManualError, "confirmation_required"):
            self.service.submit_magnet(self.profile_id, self.magnet)
        with self.assertRaisesRegex(ManualError, "force_not_required"):
            self.service.submit_magnet(
                self.profile_id,
                self.magnet,
                force=True,
                confirmed=True,
            )

    def test_authorized_watchlist_magnet_preserves_catalog_identity(self):
        content_id = self.repository.upsert_content(
            "jav", "ABC-1", "ABC-001", "Catalog item"
        )

        submitted = self.service.submit_authorized_magnet(
            self.profile_id,
            content_id,
            self.magnet,
            confirmed=True,
        )

        self.assertEqual(submitted["content_namespace"], "jav")
        self.assertEqual(submitted["content_key"], "abc-001")
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(
            self.repository.count_audit_events("authorized_watchlist_magnet_submit"),
            1,
        )

    def test_number_preview_persists_evidence_then_submits_only_stored_candidate(self):
        preview = self.service.number_preview(self.profile_id, "abc-123")

        self.assertEqual(preview["number"], "abc-123")
        self.assertEqual(preview["normalized_key"], "abc-123")
        self.assertEqual(len(preview["candidates"]), 1)
        selected = preview["candidates"][0]
        self.assertTrue(selected["accepted"])
        self.assertEqual(
            selected["destination"],
            "/configured-root/Tuntu/manual/" + "b" * 40,
        )
        self.assertIsNone(selected["duplicate"])
        self.assertEqual(self.runtime.client.submissions, [])

        submitted = self.service.submit_number_candidate(
            self.profile_id,
            run_id=preview["run_id"],
            candidate_id=selected["candidate_id"],
            confirmed=True,
        )

        detail = self.repository.get_run_detail(preview["run_id"])
        self.assertEqual(submitted["run_item_id"], detail["items"][0]["id"])
        self.assertEqual(submitted["btih"], "b" * 40)
        self.assertEqual(
            self.runtime.client.submissions,
            [("b" * 40, "/configured-root/Tuntu/manual/" + "b" * 40)],
        )
        self.assertEqual(
            self.repository.count_audit_events("manual_number_submit"), 1
        )

        duplicate_preview = self.service.number_preview(self.profile_id, "abc-123")
        duplicate = duplicate_preview["candidates"][0]["duplicate"]
        self.assertEqual(duplicate["task_id"], submitted["id"])
        self.assertEqual(duplicate["profile_id"], self.profile_id)

    def test_number_submit_rejects_unconfirmed_or_untrusted_candidate(self):
        preview = self.service.number_preview(self.profile_id, "abc-123")
        candidate_id = preview["candidates"][0]["candidate_id"]

        with self.assertRaisesRegex(ManualError, "confirmation_required"):
            self.service.submit_number_candidate(
                self.profile_id,
                run_id=preview["run_id"],
                candidate_id=candidate_id,
                confirmed=False,
            )
        with self.assertRaisesRegex(ManualError, "candidate_not_accepted"):
            self.service.submit_number_candidate(
                self.profile_id,
                run_id=preview["run_id"],
                candidate_id=candidate_id + 999,
                confirmed=True,
            )


if __name__ == "__main__":
    unittest.main()
