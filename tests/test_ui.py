from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from tuntu.api import CSRF_HEADER, build_services, create_app
from tuntu.config import StartupConfig


class UiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config = StartupConfig(data_dir=Path(self.temp_dir.name))
        self.services = build_services(config, start_scheduler=False)
        self.grant = self.services.auth.rotate_setup_token()
        self.context = TestClient(
            create_app(config, services=self.services, start_scheduler=False)
        )
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def initialize(self):
        response = self.client.post(
            "/api/v1/auth/setup",
            headers={CSRF_HEADER: "1"},
            json={
                "token": self.grant.token,
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(response.status_code, 200)

    def create_profile(self):
        return self.client.post(
            "/api/v1/profiles",
            headers={CSRF_HEADER: "1"},
            json={
                "name": "周榜预演",
                "destination_subdir": "Tuntu/weekly",
                "top_n": 20,
                "daily_time": "03:00",
                "enabled": True,
                "scope": "weekly",
                "discovery_sources": ["javdb_ranking"],
                "candidate_sources": ["javdb_detail", "knaben_api"],
                "rules": {},
                "auto_submit": False,
            },
        ).json()

    def test_first_screen_is_chinese_setup_and_protected_pages_redirect(self):
        login = self.client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn("创建管理员", login.text)
        self.assertIn("Setup Token", login.text)
        redirected = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(redirected.status_code, 303)
        self.assertEqual(redirected.headers["location"], "/login")

    def test_all_primary_pages_render_from_database_after_login(self):
        self.initialize()
        profile = self.create_profile()
        pages = {
            "/dashboard": "仪表盘",
            "/rankings": "热榜",
            "/watchlists": "关注清单",
            "/profiles": "周榜预演",
            "/profiles/new": "新建订阅",
            f"/profiles/{profile['id']}": "编辑订阅",
            "/runs": "运行记录",
            "/manual": "手工任务",
            "/downloads": "下载记录",
            "/sources": "数据源",
            "/settings": "系统设置",
        }
        for path, expected in pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn(expected, response.text)
                self.assertIn("跳到主要内容", response.text)
        settings = self.client.get("/settings").text
        self.assertIn("CloudDrive2 地址", settings)
        self.assertIn("clouddrive2.local:19798", settings)
        self.assertIn("保存到 CloudDrive2 的目录", settings)
        self.assertIn("高级设置", settings)
        self.assertIn("热榜封面显示", settings)
        self.assertIn('name="cover_display_mode"', settings)
        self.assertIn('value="none"', settings)
        self.assertIn('value="blur"', settings)
        self.assertIn('value="normal"', settings)
        self.assertIn('name="javdatabase_feed_url"', settings)
        self.assertIn('name="javdb_cookie"', settings)
        self.assertIn('name="javdb_user_agent"', settings)
        self.assertIn('class="advanced-settings"', settings)
        self.assertNotIn("gRPC Endpoint", settings)
        for field in (
            "provider_backoff_seconds",
            "provider_cache_ttl_seconds",
            "provider_min_interval_seconds",
            "provider_max_response_bytes",
            "cd2_rpc_timeout_seconds",
            "cd2_task_list_timeout_seconds",
            "cd2_check_folder_after_seconds",
            "cd2_required_stable_observations",
            "cd2_max_tree_depth",
            "cd2_max_tree_entries",
        ):
            self.assertIn(f'name="{field}"', settings)

    def test_rankings_page_obeys_none_blur_and_normal_cover_modes(self):
        self.initialize()
        profile = self.create_profile()
        run_id = self.services.repository.create_run(
            profile["id"], {"profile": profile}, trigger="manual"
        )
        content_id = self.services.repository.upsert_content(
            "jav",
            "ABC-1",
            "ABC-001",
            "Fixture title",
            metadata={
                "cover_url": "https://images.example/abc.webp",
                "source_url": "https://source.example/abc",
            },
        )
        self.services.repository.add_run_item(
            run_id,
            content_id,
            "selected",
            rankings=[
                {
                    "source": "javdatabase_weekly",
                    "rank": 1,
                    "raw_key": "ABC-1",
                    "scope": "weekly",
                }
            ],
        )
        self.services.repository.finish_run(
            run_id, "success", {"items_discovered": 1}
        )

        blurred = self.client.get("/rankings")
        self.assertEqual(blurred.status_code, 200)
        self.assertIn('class="ranking-cover cover-mode-blur"', blurred.text)
        self.assertIn('src="https://images.example/abc.webp"', blurred.text)
        self.assertIn('referrerpolicy="no-referrer"', blurred.text)

        self.services.settings.update({"cover_display_mode": "none"})
        hidden = self.client.get("/rankings")
        self.assertNotIn("https://images.example/abc.webp", hidden.text)
        self.assertIn("封面已关闭", hidden.text)

        self.services.settings.update({"cover_display_mode": "normal"})
        normal = self.client.get("/rankings")
        self.assertIn('class="ranking-cover cover-mode-normal"', normal.text)
        self.assertIn('src="https://images.example/abc.webp"', normal.text)

    def test_pages_use_external_assets_and_security_headers(self):
        self.initialize()
        page = self.client.get("/dashboard")
        self.assertIn('src="http://testserver/static/app.js?v=', page.text)
        script_url = page.text.split('src="', 1)[1].split('"', 1)[0]
        self.assertRegex(parse_qs(urlparse(script_url).query)["v"][0], r"^0\.1\.0-[0-9a-f]{12}$")
        self.assertNotIn("<script>\n", page.text)
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        css = self.client.get("/static/app.css")
        javascript = self.client.get("/static/app.js")
        self.assertEqual(css.status_code, 200)
        self.assertEqual(javascript.status_code, 200)
        self.assertIn(":focus-visible", css.text)
        self.assertIn("prefers-reduced-motion", css.text)
        self.assertIn("@media (max-width: 800px)", css.text)
        self.assertIn("confirmTwice", javascript.text)
        self.assertIn('const form = qs("#settings-form");\n  if (!form) return;', javascript.text)
        self.assertIn('error?.message ?? "请求失败，请重试。"', javascript.text)
        sources = self.client.get("/sources").text
        self.assertIn("单标识探针", sources)
        self.assertIn('data-probe-mode="query"', sources)

    def test_terminal_run_detail_renders_empty_items_instead_of_dict_method(self):
        self.initialize()
        profile = self.create_profile()
        run_id = self.services.repository.create_run(
            profile["id"], {"profile": profile}, trigger="manual"
        )
        self.services.repository.finish_run(
            run_id,
            "success",
            {"items_discovered": 0, "items_processed": 0},
        )

        response = self.client.get(f"/runs/{run_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("没有内容条目", response.text)
        self.assertIn(run_id, response.text)

    def test_login_page_switches_after_initialization_and_active_session_redirects(self):
        self.initialize()
        response = self.client.get("/login", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.client.cookies.clear()
        login = self.client.get("/login")
        self.assertIn("管理员登录", login.text)
        self.assertNotIn("创建管理员", login.text)

    def test_cli_reset_token_exposes_reset_form_and_revokes_old_session(self):
        self.initialize()
        reset = self.services.auth.rotate_setup_token()
        self.client.cookies.clear()

        page = self.client.get("/login")
        self.assertIn("重置管理员密码", page.text)
        self.assertIn("原管理员名称", page.text)
        response = self.client.post(
            "/api/v1/auth/setup",
            headers={CSRF_HEADER: "1"},
            json={
                "token": reset.token,
                "username": "admin",
                "password": "replacement-horse-battery-staple",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login",
                headers={CSRF_HEADER: "1"},
                json={
                    "username": "admin",
                    "password": "correct-horse-battery-staple",
                },
            ).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
