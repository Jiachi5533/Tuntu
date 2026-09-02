from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tuntu.db import Database, Repository
from tuntu.db.migration import migrate_database
from tuntu.profiles import ProfileError, ProfileService


class ProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        migrate_database(root / "tuntu.db", root / "backups")
        self.database = Database(root / "tuntu.db")
        self.repository = Repository(self.database)
        self.sync_count = 0
        self.service = ProfileService(
            self.repository, scheduler_sync=self._sync
        )

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()

    def _sync(self):
        self.sync_count += 1

    @staticmethod
    def valid(**overrides):
        value = {
            "name": "周榜预演",
            "destination_subdir": "Tuntu/weekly",
            "top_n": 20,
            "daily_time": "03:00",
            "enabled": True,
            "scope": "weekly",
            "discovery_sources": ["javdb_ranking", "javdatabase_weekly"],
            "candidate_sources": ["javdb_detail", "knaben_api"],
            "rules": {},
            "auto_submit": False,
        }
        value.update(overrides)
        return value

    def test_create_defaults_to_dry_run_and_update_is_partial(self):
        created = self.service.create(self.valid())
        self.assertFalse(created["auto_submit"])
        updated = self.service.update(created["id"], {"name": "新名称"})
        self.assertEqual(updated["name"], "新名称")
        self.assertEqual(updated["candidate_sources"], ["javdb_detail", "knaben_api"])
        self.assertTrue(updated["watchlist_compatible"])
        self.assertEqual(self.sync_count, 2)

    def test_javdb_web_candidate_is_watchlist_compatible_by_itself(self):
        created = self.service.create(
            self.valid(candidate_sources=["javdb_detail"])
        )

        self.assertTrue(created["watchlist_compatible"])

    def test_enabled_profile_requires_both_source_kinds(self):
        for field in ("discovery_sources", "candidate_sources"):
            with self.subTest(field=field), self.assertRaisesRegex(
                ProfileError, "enabled_profile_requires_sources"
            ):
                self.service.create(self.valid(**{field: []}))

    def test_source_scope_must_match_profile_scope(self):
        with self.assertRaisesRegex(ProfileError, "source_scope_mismatch"):
            self.service.create(
                self.valid(scope="daily", discovery_sources=["javdatabase_weekly"])
            )

    def test_rules_and_paths_are_bounded(self):
        invalid = (
            (self.valid(destination_subdir="../escape"), "invalid_destination_subdir"),
            (self.valid(top_n=101), "invalid_top_n"),
            (self.valid(daily_time="25:00"), "invalid_daily_time"),
            (self.valid(rules={"unknown": True}), "invalid_rules"),
            (
                self.valid(rules={"include_keywords": ["x" * 101]}),
                "invalid_rules",
            ),
        )
        for payload, code in invalid:
            with self.subTest(code=code), self.assertRaisesRegex(ProfileError, code):
                self.service.create(payload)

    def test_create_and_partial_update_reject_unknown_fields(self):
        with self.assertRaisesRegex(ProfileError, "invalid_request"):
            self.service.create(self.valid(destination_template="/hard-coded"))

        profile_id = self.service.create(self.valid())["id"]
        with self.assertRaisesRegex(ProfileError, "invalid_request"):
            self.service.update(profile_id, {"destination_template": "/hard-coded"})

    def test_archive_hides_by_default_and_restore_keeps_history(self):
        profile_id = self.service.create(self.valid())["id"]
        self.service.archive(profile_id)
        self.assertEqual(self.service.list(page=1, page_size=20, include_archived=False)["total"], 0)
        self.assertEqual(self.service.list(page=1, page_size=20, include_archived=True)["total"], 1)
        restored = self.service.restore(profile_id)
        self.assertIsNone(restored["archived_at"])


if __name__ == "__main__":
    unittest.main()
