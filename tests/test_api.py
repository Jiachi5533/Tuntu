from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tuntu.api import CSRF_HEADER, build_services, create_app, csv_safe
from tuntu.config import StartupConfig


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = StartupConfig(data_dir=Path(self.temp_dir.name))
        self.services = build_services(self.config, start_scheduler=False)
        self.setup_grant = self.services.auth.rotate_setup_token()
        self.app = create_app(
            self.config, services=self.services, start_scheduler=False
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    @property
    def write_headers(self):
        return {CSRF_HEADER: "1"}

    def initialize(self):
        response = self.client.post(
            "/api/v1/auth/setup",
            headers=self.write_headers,
            json={
                "token": self.setup_grant.token,
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_health_ready_and_bootstrap_status_are_public(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertFalse(ready.json()["runtime_configured"])
        status = self.client.get("/api/v1/auth/status").json()
        self.assertFalse(status["initialized"])
        self.assertTrue(status["setup_token_active"])

    def test_unsafe_request_requires_same_origin_or_custom_header(self):
        blocked = self.client.post(
            "/api/v1/auth/setup",
            json={
                "token": self.setup_grant.token,
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["error"]["code"], "csrf_required")

        allowed = self.client.post(
            "/api/v1/auth/setup",
            headers={"Origin": "http://testserver"},
            json={
                "token": self.setup_grant.token,
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_setup_login_logout_cookie_and_protected_resource(self):
        setup = self.initialize()
        cookie = setup.headers["set-cookie"].casefold()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertNotIn("correct-horse", setup.text)
        refreshed = self.client.get("/api/v1/auth/me")
        self.assertEqual(refreshed.json()["username"], "admin")
        self.assertIn("max-age=604800", refreshed.headers["set-cookie"].casefold())

        logout = self.client.post(
            "/api/v1/auth/logout", headers=self.write_headers
        )
        self.assertEqual(logout.status_code, 200)
        self.assertIn("max-age=0", logout.headers["set-cookie"].casefold())
        denied = self.client.get("/api/v1/settings")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["error"]["code"], "authentication_required")

        login = self.client.post(
            "/api/v1/auth/login",
            headers=self.write_headers,
            json={
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(login.status_code, 200)
        self.assertNotIn("tuntu_session", login.text)

    def test_password_change_revokes_current_session(self):
        self.initialize()
        changed = self.client.post(
            "/api/v1/auth/password",
            headers=self.write_headers,
            json={
                "current_password": "correct-horse-battery-staple",
                "new_password": "new-correct-horse-battery-staple",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_profile_crud_archive_restore_pagination_and_audit(self):
        self.initialize()
        payload = {
            "name": "JavDB 周榜预演",
            "destination_subdir": "Tuntu/weekly",
            "top_n": 20,
            "daily_time": "03:10",
            "enabled": True,
            "scope": "weekly",
            "discovery_sources": ["javdb_ranking", "javdatabase_weekly"],
            "candidate_sources": ["javdb_detail", "knaben_api"],
            "rules": {"min_seeders": 1, "uhd": "exclude"},
            "auto_submit": False,
        }
        created = self.client.post(
            "/api/v1/profiles", headers=self.write_headers, json=payload
        )
        self.assertEqual(created.status_code, 201, created.text)
        profile_id = created.json()["id"]
        self.assertFalse(created.json()["auto_submit"])

        listed = self.client.get("/api/v1/profiles?page=1&page_size=1").json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["id"], profile_id)

        updated = self.client.put(
            f"/api/v1/profiles/{profile_id}",
            headers=self.write_headers,
            json={"enabled": False, "name": "暂停中的订阅"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertFalse(updated.json()["enabled"])

        archived = self.client.post(
            f"/api/v1/profiles/{profile_id}/archive", headers=self.write_headers
        )
        self.assertIsNotNone(archived.json()["archived_at"])
        self.assertEqual(self.client.get("/api/v1/profiles").json()["total"], 0)
        restored = self.client.post(
            f"/api/v1/profiles/{profile_id}/restore", headers=self.write_headers
        )
        self.assertIsNone(restored.json()["archived_at"])
        self.assertEqual(self.services.repository.count_audit_events("profile_created"), 1)

    def test_profile_validation_has_stable_error_code(self):
        self.initialize()
        response = self.client.post(
            "/api/v1/profiles",
            headers=self.write_headers,
            json={
                "name": "bad",
                "destination_subdir": "../escape",
                "enabled": True,
                "discovery_sources": [],
                "candidate_sources": [],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_destination_subdir")

    def test_settings_secret_is_never_returned_and_unknown_field_fails(self):
        self.initialize()
        secret = "fixture-api-token-never-return"
        saved = self.client.put(
            "/api/v1/settings",
            headers=self.write_headers,
            json={
                "cd2_endpoint": "grpc://nas.invalid:19798",
                "cd2_auth_mode": "api_token",
                "cd2_root": "/",
                "cd2_api_token": secret,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["cd2_api_token_configured"])
        self.assertNotIn(secret, saved.text)
        self.assertNotIn(secret, self.client.get("/api/v1/settings").text)

        invalid = self.client.put(
            "/api/v1/settings",
            headers=self.write_headers,
            json={"developer_nas_path": "/115open"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "unknown_setting")

    def test_all_advanced_runtime_values_are_configurable(self):
        self.initialize()
        values = {
            "provider_backoff_seconds": 0.5,
            "provider_cache_ttl_seconds": 120,
            "provider_min_interval_seconds": 1.5,
            "provider_max_response_bytes": 2_000_000,
            "cd2_rpc_timeout_seconds": 9,
            "cd2_task_list_timeout_seconds": 12,
            "cd2_poll_interval_seconds": 240,
            "cd2_attention_after_hours": 18,
            "cd2_check_folder_after_seconds": 7,
            "cd2_required_stable_observations": 3,
            "cd2_max_tree_depth": 6,
            "cd2_max_tree_entries": 8_000,
        }
        response = self.client.put(
            "/api/v1/settings", headers=self.write_headers, json=values
        )
        self.assertEqual(response.status_code, 200, response.text)
        for key, value in values.items():
            self.assertEqual(response.json()[key], value)

    def test_direct_magnet_submit_requires_server_side_confirmation(self):
        self.initialize()
        profile = self.client.post(
            "/api/v1/profiles",
            headers=self.write_headers,
            json={
                "name": "Manual fixture",
                "destination_subdir": "Tuntu/manual",
                "enabled": False,
                "scope": "weekly",
                "discovery_sources": [],
                "candidate_sources": [],
                "rules": {},
                "auto_submit": False,
            },
        ).json()
        response = self.client.post(
            "/api/v1/manual/magnet/submit",
            headers=self.write_headers,
            json={
                "profile_id": profile["id"],
                "magnet_uri": "magnet:?xt=urn:btih:" + "a" * 40,
                "confirmed": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "confirmation_required")

    def test_lists_are_paginated_and_missing_objects_have_stable_codes(self):
        self.initialize()
        runs = self.client.get("/api/v1/runs?page=1&page_size=5")
        self.assertEqual(runs.json()["total"], 0)
        downloads = self.client.get("/api/v1/downloads?page=1&page_size=5")
        self.assertEqual(downloads.json()["total"], 0)
        self.assertEqual(
            self.client.get("/api/v1/runs/missing").json()["error"]["code"],
            "run_not_found",
        )
        self.assertEqual(
            self.client.get("/api/v1/downloads/missing").json()["error"]["code"],
            "download_not_found",
        )

    def test_source_can_be_disabled_and_enabled_without_deleting_profiles(self):
        self.initialize()
        disabled = self.client.put(
            "/api/v1/sources/sukebei_rss",
            headers=self.write_headers,
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["enabled"])
        catalog = self.client.get("/api/v1/sources").json()["items"]
        self.assertFalse(
            next(item for item in catalog if item["name"] == "sukebei_rss")[
                "enabled"
            ]
        )
        enabled = self.client.put(
            "/api/v1/sources/sukebei_rss",
            headers=self.write_headers,
            json={"enabled": True},
        )
        self.assertTrue(enabled.json()["enabled"])
        self.assertEqual(
            self.services.repository.count_audit_events("source_disabled"), 1
        )

    def test_csv_has_bom_headers_and_formula_escaping(self):
        self.initialize()
        response = self.client.get("/api/v1/exports/downloads.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("内容标识", response.content.decode("utf-8-sig"))
        self.assertEqual(csv_safe("=cmd|x"), "'=cmd|x")
        self.assertEqual(csv_safe("safe"), "safe")

    def test_openapi_contains_no_runtime_secrets(self):
        self.initialize()
        schema = self.client.get("/openapi.json").text
        self.assertNotIn(self.setup_grant.token, schema)
        self.assertNotIn("correct-horse-battery-staple", schema)


if __name__ == "__main__":
    unittest.main()
