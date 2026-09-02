from __future__ import annotations

import tempfile
import unittest
from importlib import resources
from pathlib import Path

from fastapi.testclient import TestClient

from tuntu.api import CSRF_HEADER, build_services, create_app
from tuntu.config import StartupConfig


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_dockerfile_is_non_root_health_checked_and_portable(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn('ENTRYPOINT ["tuntu"]', dockerfile)
        self.assertNotIn("--platform=linux/amd64", dockerfile)
        self.assertNotIn("/115open", dockerfile)
        self.assertNotIn("192.168.", dockerfile)

    def test_product_files_do_not_embed_a_private_nas_or_cloud_path(self):
        product_files = [
            *ROOT.joinpath("src", "tuntu").rglob("*.py"),
            *ROOT.joinpath("src", "tuntu", "templates").rglob("*.html"),
            *ROOT.joinpath("src", "tuntu", "static").rglob("*.js"),
        ]
        combined = "\n".join(path.read_text(errors="ignore") for path in product_files)
        self.assertNotIn("192.168.", combined)
        self.assertNotIn("/115open", combined)

    def test_compose_has_persistent_data_and_hardening(self):
        compose = (ROOT / "compose.yaml").read_text()
        for expected in (
            "tuntu-data:/data",
            "read_only: true",
            "no-new-privileges:true",
            "cap_drop:",
            "stop_grace_period: 30s",
        ):
            self.assertIn(expected, compose)
        self.assertNotIn("privileged: true", compose)

    def test_ci_builds_amd64_and_arm64_without_publishing(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("linux/amd64,linux/arm64", workflow)
        self.assertIn("push: false", workflow)

    def test_installed_package_contains_ui_and_migrations(self):
        package = resources.files("tuntu")
        for relative in (
            "templates/base.html",
            "static/app.css",
            "static/app.js",
            "db/alembic/versions/0004_run_item_duplicate_link.py",
        ):
            self.assertTrue(package.joinpath(relative).is_file(), relative)

    def test_restart_keeps_admin_profile_settings_and_ready_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = StartupConfig(data_dir=Path(temporary))
            first_services = build_services(config, start_scheduler=False)
            grant = first_services.auth.rotate_setup_token()
            with TestClient(
                create_app(
                    config,
                    services=first_services,
                    start_scheduler=False,
                )
            ) as first:
                setup = first.post(
                    "/api/v1/auth/setup",
                    headers={CSRF_HEADER: "1"},
                    json={
                        "token": grant.token,
                        "username": "admin",
                        "password": "correct-horse-battery-staple",
                    },
                )
                self.assertEqual(setup.status_code, 200)
                profile = first.post(
                    "/api/v1/profiles",
                    headers={CSRF_HEADER: "1"},
                    json={
                        "name": "Restart fixture",
                        "destination_subdir": "Tuntu/restart",
                        "top_n": 7,
                        "enabled": True,
                        "scope": "weekly",
                        "discovery_sources": ["javdb_ranking"],
                        "candidate_sources": ["knaben_api"],
                        "rules": {},
                        "auto_submit": False,
                    },
                ).json()
                first.put(
                    "/api/v1/settings",
                    headers={CSRF_HEADER: "1"},
                    json={"provider_retries": 4},
                )

            second_services = build_services(config, start_scheduler=False)
            with TestClient(
                create_app(
                    config,
                    services=second_services,
                    start_scheduler=False,
                )
            ) as second:
                login = second.post(
                    "/api/v1/auth/login",
                    headers={CSRF_HEADER: "1"},
                    json={
                        "username": "admin",
                        "password": "correct-horse-battery-staple",
                    },
                )
                self.assertEqual(login.status_code, 200)
                restored = second.get(
                    f"/api/v1/profiles/{profile['id']}"
                ).json()
                self.assertEqual(restored["name"], "Restart fixture")
                self.assertEqual(restored["top_n"], 7)
                self.assertEqual(
                    second.get("/api/v1/settings").json()["provider_retries"], 4
                )
                ready = second.get("/ready").json()
                self.assertEqual(ready["status"], "ready")


if __name__ == "__main__":
    unittest.main()
