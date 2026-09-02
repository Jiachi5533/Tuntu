from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tuntu.db import Database, Repository
from tuntu.db.migration import migrate_database
from tuntu.downloaders.clouddrive import (
    CloudDriveHealth,
    DirectorySnapshot,
)
from tuntu.settings import SettingsError, SettingsService


class FakeCloudDriveClient:
    instances = []

    def __init__(self, config):
        self.config = config
        self.destinations = []
        self.closed = False
        self.__class__.instances.append(self)

    def health_check(self):
        return CloudDriveHealth("CloudDrive2", "fixture", "1.0.14")

    def ensure_destination(self, destination):
        self.destinations.append(destination)

    def snapshot(self, destination, *, force_refresh):
        return DirectorySnapshot(())

    def close(self):
        self.closed = True


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "tuntu.db"
        migrate_database(self.db_path, root / "backups")
        self.database = Database(self.db_path)
        self.repo = Repository(self.database)
        FakeCloudDriveClient.instances = []

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def configured_values(**overrides):
        values = {
            "timezone": "UTC",
            "cd2_endpoint": "grpc://nas.invalid:19798",
            "cd2_auth_mode": "api_token",
            "cd2_api_token": "fixture-secret-token",
            "cd2_root": "/api-root",
            "cd2_test_subdir": ".test",
        }
        values.update(overrides)
        return values

    def test_settings_persist_and_public_view_never_contains_secrets(self):
        service = SettingsService(self.repo)
        service.update(
            self.configured_values(
                cd2_password="fixture-password",
                javdb_cookie="fixture-javdb-session",
                authorized_candidate_api_url="https://authorized.example/api",
                authorized_candidate_api_token="authorized-secret",
            )
        )

        restarted = SettingsService(Repository(Database(self.db_path)))
        try:
            public = restarted.get_public()
            effective = restarted.get_effective()
        finally:
            restarted.repository.database.dispose()

        self.assertTrue(public["cd2_api_token_configured"])
        self.assertTrue(public["cd2_password_configured"])
        self.assertNotIn("cd2_api_token", public)
        self.assertNotIn("cd2_password", public)
        self.assertNotIn("fixture-secret-token", repr(public))
        self.assertTrue(public["javdb_cookie_configured"])
        self.assertNotIn("fixture-javdb-session", repr(public))
        self.assertTrue(public["authorized_candidate_api_token_configured"])
        self.assertNotIn("authorized-secret", repr(public))
        self.assertEqual(effective["cd2_api_token"], "fixture-secret-token")
        self.assertEqual(
            effective["authorized_candidate_api_url"],
            "https://authorized.example/api",
        )

    def test_new_install_defaults_to_username_and_password(self):
        public = SettingsService(self.repo).get_public()

        self.assertEqual(public["cd2_auth_mode"], "user_password")
        self.assertEqual(public["cover_display_mode"], "blur")
        self.assertEqual(public["javdb_base_url"], "https://javdb.com")
        self.assertIn("Mozilla/5.0", public["javdb_user_agent"])

    def test_cover_mode_and_provider_endpoints_are_configurable_and_validated(self):
        service = SettingsService(self.repo)

        updated = service.update(
            {
                "cover_display_mode": "none",
                "javdb_base_url": "https://javdb.example.test",
                "javdb_user_agent": "Fixture Browser/1.0",
                "javdatabase_feed_url": "https://javdatabase.example.test/feed/",
                "sukebei_feed_url": "https://sukebei.example.test/",
                "knaben_api_url": "https://knaben.example.test/v1",
                "bitsearch_api_url": "https://bitsearch.example.test/api/search",
            }
        )

        self.assertEqual(updated["cover_display_mode"], "none")
        self.assertEqual(updated["javdb_base_url"], "https://javdb.example.test")
        self.assertEqual(updated["javdb_user_agent"], "Fixture Browser/1.0")
        with self.assertRaisesRegex(SettingsError, "invalid_cover_display_mode"):
            service.update({"cover_display_mode": "reveal-on-hover"})
        with self.assertRaisesRegex(SettingsError, "invalid_javdb_base_url"):
            service.update({"javdb_base_url": "file:///etc/passwd"})

    def test_omitted_secret_is_preserved_and_explicit_none_clears_it(self):
        service = SettingsService(self.repo)
        service.update(self.configured_values())

        service.update({"provider_retries": 4})
        self.assertEqual(
            service.get_effective()["cd2_api_token"], "fixture-secret-token"
        )
        service.update({"cd2_endpoint": None, "cd2_api_token": None})
        self.assertFalse(service.get_public()["cd2_api_token_configured"])

    def test_disabled_sources_are_persisted_and_validated(self):
        service = SettingsService(self.repo)
        service.update({"disabled_sources": ["sukebei_rss"]})
        self.assertEqual(service.get_public()["disabled_sources"], ["sukebei_rss"])
        with self.assertRaisesRegex(SettingsError, "invalid_disabled_sources"):
            service.update({"disabled_sources": ["unknown-source"]})

    def test_environment_override_wins_without_overwriting_stored_value(self):
        SettingsService(self.repo).update(self.configured_values(cd2_root="/stored"))
        service = SettingsService(
            self.repo,
            environment_overrides={
                "cd2_root": "/environment",
                "cd2_api_token": "environment-secret",
            },
        )

        self.assertEqual(service.get_effective()["cd2_root"], "/environment")
        self.assertEqual(service.get_stored()["cd2_root"], "/stored")
        self.assertEqual(
            service.get_public()["environment_overrides"],
            ["cd2_api_token", "cd2_root"],
        )

    def test_two_different_deployments_build_without_hardcoded_paths(self):
        first = SettingsService(self.repo)
        first.update(self.configured_values())
        first_config = first.build_clouddrive_config()

        second = SettingsService(
            self.repo,
            environment_overrides={
                "cd2_endpoint": "grpcs://other.invalid:9443",
                "cd2_root": "/different-root",
                "cd2_api_token": "different-secret",
            },
        )
        second_config = second.build_clouddrive_config()

        self.assertEqual(first_config.target, "nas.invalid:19798")
        self.assertEqual(first_config.root_path, "/api-root")
        self.assertEqual(second_config.target, "other.invalid:9443")
        self.assertEqual(second_config.root_path, "/different-root")

    def test_connection_test_checks_create_and_read_permissions_then_closes(self):
        service = SettingsService(
            self.repo, client_factory=FakeCloudDriveClient
        )
        service.update(self.configured_values())

        result = service.test_clouddrive()

        client = FakeCloudDriveClient.instances[-1]
        self.assertEqual(result.api_version, "1.0.14")
        self.assertEqual(result.test_destination, "/api-root/.test")
        self.assertEqual(client.destinations, ["/api-root/.test"])
        self.assertTrue(client.closed)

    def test_invalid_values_fail_before_network_and_unconfigured_is_explicit(self):
        service = SettingsService(self.repo)
        with self.assertRaisesRegex(SettingsError, "invalid_timezone"):
            service.update({"timezone": "Invalid/Timezone"})
        with self.assertRaisesRegex(SettingsError, "invalid_clouddrive_settings"):
            service.update(
                self.configured_values(cd2_endpoint="http://nas.invalid/path")
            )
        with self.assertRaisesRegex(SettingsError, "cd2_not_configured"):
            service.build_clouddrive_config()
        with self.assertRaisesRegex(SettingsError, "invalid_cd2_poll_interval_seconds"):
            service.update({"cd2_poll_interval_seconds": 0})
        with self.assertRaisesRegex(SettingsError, "invalid_cd2_api_token"):
            service.update({"cd2_api_token": "x" * 4_001})


if __name__ == "__main__":
    unittest.main()
