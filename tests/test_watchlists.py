from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tuntu.api import CSRF_HEADER, build_services, create_app
from tuntu.config import StartupConfig
from tuntu.runs.service import RunExecution
from tuntu.watchlists import WatchlistError


class FakeWatchlistRunService:
    def __init__(self):
        self.calls = []

    def execute(self, profile_id, **options):
        self.calls.append((profile_id, options))
        return RunExecution("run-fixture", "success")


class FakeWatchlistRuntime:
    def __init__(self):
        self.run_service = FakeWatchlistRunService()

    def require(self):
        return self


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config = StartupConfig(data_dir=Path(self.temp_dir.name))
        self.services = build_services(config, start_scheduler=False)

    def tearDown(self):
        self.services.runtime.close()
        self.services.database.dispose()
        self.temp_dir.cleanup()

    def test_create_import_and_track_metadata_only_items(self):
        watchlist = self.services.watchlists.create(
            {
                "name": "水嶋あずみ作品清单",
                "subject_type": "person",
                "query": "水嶋あずみ",
                "aliases": ["水岛津实", "水嶋津实"],
            }
        )

        result = self.services.watchlists.import_items(
            watchlist["id"],
            source_name="catalog_fixture",
            items=[
                {
                    "namespace": "jav",
                    "key": "sun-16",
                    "title": "Fixture title",
                    "metadata": {
                        "source_url": "https://catalog.example/SUN-016",
                        "release_date": "2012-01-01",
                    },
                },
                {
                    "namespace": "jav",
                    "key": "SUN-016",
                    "title": "Fixture title (duplicate)",
                    "metadata": {},
                },
            ],
        )

        self.assertEqual(result["imported"], 1)
        detail = self.services.watchlists.get(watchlist["id"])
        self.assertEqual(detail["summary"], {"total": 1, "pending": 1})
        self.assertEqual(detail["items"][0]["normalized_key"], "sun-016")
        self.assertEqual(detail["items"][0]["state"], "pending")
        self.assertEqual(detail["items"][0]["download"], None)

        updated = self.services.watchlists.set_item_state(
            watchlist["id"], detail["items"][0]["content_item_id"], "owned"
        )
        self.assertEqual(updated["state"], "owned")

        with self.assertRaises(WatchlistError) as caught:
            self.services.watchlists.submit_authorized_magnet(
                watchlist["id"],
                detail["items"][0]["content_item_id"],
                profile_id=1,
                magnet_uri="magnet:?xt=urn:btih:" + "a" * 40,
                rights_confirmed=False,
                confirmed=True,
            )
        self.assertEqual(caught.exception.code, "rights_confirmation_required")

    def test_import_rejects_download_links_hidden_in_metadata(self):
        watchlist = self.services.watchlists.create(
            {
                "name": "Metadata only",
                "subject_type": "keyword",
                "query": "fixture",
                "aliases": [],
            }
        )

        with self.assertRaises(WatchlistError) as caught:
            self.services.watchlists.import_items(
                watchlist["id"],
                source_name="unsafe_fixture",
                items=[
                    {
                        "namespace": "general",
                        "key": "fixture-1",
                        "title": "Fixture",
                        "metadata": {"magnet_uri": "magnet:?xt=urn:btih:" + "a" * 40},
                    }
                ],
            )

        self.assertEqual(caught.exception.code, "metadata_only_required")

    def test_import_rejects_non_http_display_urls(self):
        watchlist = self.services.watchlists.create(
            {
                "name": "Safe links",
                "subject_type": "series",
                "query": "fixture",
                "aliases": [],
            }
        )

        with self.assertRaises(WatchlistError) as caught:
            self.services.watchlists.import_items(
                watchlist["id"],
                source_name="unsafe_fixture",
                items=[
                    {
                        "namespace": "general",
                        "key": "fixture-1",
                        "title": "Fixture",
                        "metadata": {"source_url": "javascript:alert(1)"},
                    }
                ],
            )

        self.assertEqual(caught.exception.code, "invalid_metadata_url")

    def test_automation_requires_explicit_rights_confirmation_and_runs_pending_items(self):
        profile_id = self.services.repository.create_profile(
            "Authorized fixture",
            {
                "scope": "weekly",
                "discovery_sources": ["javdb_ranking"],
                "candidate_sources": ["knaben_api"],
                "rules": {},
                "auto_submit": False,
            },
            destination_subdir="authorized",
            top_n=10,
        )
        runtime = FakeWatchlistRuntime()
        sync_calls = []
        service = self.services.watchlists.__class__(
            self.services.repository,
            runtime=runtime,
            scheduler_sync=lambda: sync_calls.append(True),
        )
        watchlist = service.create(
            {
                "name": "Authorized catalog",
                "subject_type": "series",
                "query": "Fixture",
                "aliases": [],
            }
        )
        imported = service.import_items(
            watchlist["id"],
            source_name="authorized_catalog",
            items=[
                {"namespace": "jav", "key": "ABC-1", "title": "One"},
                {"namespace": "jav", "key": "XYZ-2", "title": "Two"},
            ],
        )
        service.set_item_state(
            watchlist["id"], imported["items"][1]["content_item_id"], "owned"
        )

        with self.assertRaises(WatchlistError) as caught:
            service.configure_automation(
                watchlist["id"],
                {
                    "profile_id": profile_id,
                    "daily_time": "04:30",
                    "enabled": True,
                    "auto_submit": True,
                    "rights_confirmed": False,
                },
            )
        self.assertEqual(caught.exception.code, "rights_confirmation_required")

        configured = service.configure_automation(
            watchlist["id"],
            {
                "profile_id": profile_id,
                "daily_time": "04:30",
                "enabled": True,
                "auto_submit": True,
                "rights_confirmed": True,
            },
        )
        self.assertEqual(
            configured["automation"],
            {
                "profile_id": profile_id,
                "daily_time": "04:30",
                "enabled": True,
                "auto_submit": True,
            },
        )
        self.assertEqual(sync_calls, [True])

        execution = service.run(watchlist["id"], force_dry_run=False)

        self.assertEqual(execution.run_id, "run-fixture")
        self.assertEqual(
            runtime.run_service.calls,
            [
                (
                    profile_id,
                    {
                        "trigger": "watchlist",
                        "force_dry_run": False,
                        "manual_raw_keys": ["ABC-1"],
                        "auto_submit_override": True,
                    },
                )
            ],
        )

    def test_automation_rejects_missing_profile_and_invalid_time(self):
        javdb_profile = self.services.repository.create_profile(
            "JavDB web",
            {
                "candidate_sources": ["javdb_detail"],
                "discovery_sources": ["javdb_ranking"],
            },
            destination_subdir="linked",
        )
        watchlist = self.services.watchlists.create(
            {
                "name": "Fixture",
                "subject_type": "keyword",
                "query": "fixture",
                "aliases": [],
            }
        )
        for payload, code in (
            (
                {
                    "profile_id": 999,
                    "daily_time": "04:30",
                    "enabled": True,
                    "auto_submit": False,
                    "rights_confirmed": False,
                },
                "profile_not_found",
            ),
            (
                {
                    "profile_id": None,
                    "daily_time": "25:00",
                    "enabled": False,
                    "auto_submit": False,
                    "rights_confirmed": False,
                },
                "invalid_daily_time",
            ),
        ):
            with self.subTest(code=code), self.assertRaises(WatchlistError) as caught:
                self.services.watchlists.configure_automation(watchlist["id"], payload)
            self.assertEqual(caught.exception.code, code)

        configured = self.services.watchlists.configure_automation(
            watchlist["id"],
            {
                "profile_id": javdb_profile,
                "daily_time": "04:30",
                "enabled": True,
                "auto_submit": False,
                "rights_confirmed": False,
            },
        )
        self.assertEqual(configured["automation"]["profile_id"], javdb_profile)


class WatchlistApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config = StartupConfig(data_dir=Path(self.temp_dir.name))
        self.services = build_services(config, start_scheduler=False)
        self.grant = self.services.auth.rotate_setup_token()
        self.context = TestClient(
            create_app(config, services=self.services, start_scheduler=False)
        )
        self.client = self.context.__enter__()
        response = self.client.post(
            "/api/v1/auth/setup",
            headers={CSRF_HEADER: "1"},
            json={
                "token": self.grant.token,
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_watchlist_api_and_pages_are_real_database_backed(self):
        created = self.client.post(
            "/api/v1/watchlists",
            headers={CSRF_HEADER: "1"},
            json={
                "name": "水嶋あずみ作品清单",
                "subject_type": "person",
                "query": "水嶋あずみ",
                "aliases": ["水岛津实", "水嶋津实"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        watchlist_id = created.json()["id"]

        imported = self.client.post(
            f"/api/v1/watchlists/{watchlist_id}/items/import",
            headers={CSRF_HEADER: "1"},
            json={
                "source_name": "manual_metadata",
                "items": [
                    {
                        "namespace": "jav",
                        "key": "SUN-016",
                        "title": "Fixture",
                        "metadata": {"source_url": "https://catalog.example/SUN-016"},
                    }
                ],
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)

        listing = self.client.get("/watchlists")
        detail = self.client.get(f"/watchlists/{watchlist_id}")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn("关注清单", listing.text)
        self.assertIn("水嶋あずみ作品清单", listing.text)
        self.assertIn("SUN-016", detail.text)
        self.assertIn("只保存元数据", detail.text)
        self.assertIn("我确认有权使用所填链接", detail.text)

        item_id = imported.json()["items"][0]["content_item_id"]
        state = self.client.patch(
            f"/api/v1/watchlists/{watchlist_id}/items/{item_id}",
            headers={CSRF_HEADER: "1"},
            json={"state": "ignored"},
        )
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["state"], "ignored")

    def test_watchlist_automation_api_can_save_and_run_a_dry_run(self):
        profile_id = self.services.repository.create_profile(
            "Fixture download policy",
            {
                "scope": "weekly",
                "discovery_sources": ["javdb_ranking"],
                "candidate_sources": ["knaben_api"],
                "rules": {},
                "auto_submit": False,
            },
            destination_subdir="fixture",
            top_n=20,
        )
        watchlist = self.services.watchlists.create(
            {
                "name": "Automated fixture",
                "subject_type": "keyword",
                "query": "fixture",
                "aliases": [],
            }
        )
        self.services.watchlists.import_items(
            watchlist["id"],
            source_name="authorized_fixture",
            items=[{"namespace": "jav", "key": "ABC-1", "title": "One"}],
        )

        saved = self.client.put(
            f"/api/v1/watchlists/{watchlist['id']}/automation",
            headers={CSRF_HEADER: "1"},
            json={
                "profile_id": profile_id,
                "daily_time": "04:30",
                "enabled": True,
                "auto_submit": False,
                "rights_confirmed": False,
            },
        )

        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["automation"]["enabled"])
        self.services.watchlists.runtime = FakeWatchlistRuntime()
        run = self.client.post(
            f"/api/v1/watchlists/{watchlist['id']}/run",
            headers={CSRF_HEADER: "1"},
            json={"force_dry_run": True},
        )
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run_id"], "run-fixture")

        page = self.client.get(f"/watchlists/{watchlist['id']}")
        self.assertIn("自动处理", page.text)
        self.assertIn("Fixture download policy", page.text)
        self.assertIn('value="04:30"', page.text)


if __name__ == "__main__":
    unittest.main()
