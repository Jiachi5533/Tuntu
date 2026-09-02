from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tuntu import __version__
from tuntu.auth import AuthError


PACKAGE_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")


def _asset_version() -> str:
    digest = hashlib.sha256()
    for name in ("app.css", "app.js"):
        digest.update((PACKAGE_ROOT / "static" / name).read_bytes())
    return f"{__version__}-{digest.hexdigest()[:12]}"


ASSET_VERSION = _asset_version()


def _user(request: Request):
    token = request.cookies.get("tuntu_session")
    try:
        user = request.app.state.services.auth.authenticate(token)
        request.state.session_token_to_refresh = token
        return user
    except AuthError:
        return None


def _context(request: Request, *, page: str, title: str, **values):
    return {
        "request": request,
        "page": page,
        "title": title,
        "user": _user(request),
        "asset_version": ASSET_VERSION,
        **values,
    }


def install_ui(app: FastAPI) -> None:
    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_ROOT / "static"),
        name="static",
    )

    def protected(request: Request):
        user = _user(request)
        if user is None:
            return None, RedirectResponse("/login", status_code=303)
        return user, None

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if _user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        services = request.app.state.services
        status = services.auth.bootstrap_status()
        if status.initialized and _user(request) is not None:
            return RedirectResponse("/dashboard", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context=_context(
                request,
                page="auth",
                title="首次设置" if not status.initialized else "登录",
                initialized=status.initialized,
                setup_token_active=status.setup_token_active,
            ),
        )

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        repository = request.app.state.services.repository
        recent_runs, _ = repository.list_runs(limit=6)
        recent_downloads, _ = repository.list_downloads(limit=6)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=_context(
                request,
                page="dashboard",
                title="仪表盘",
                summary=repository.dashboard_summary(),
                runs=recent_runs,
                downloads=recent_downloads,
                sources=request.app.state.services.runtime.source_catalog(),
                runtime=request.app.state.services.runtime,
            ),
        )

    @app.get("/rankings", include_in_schema=False)
    def rankings_page(request: Request, profile_id: int | None = None):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        services = request.app.state.services
        profiles = services.profiles.list(
            page=1, page_size=100, include_archived=True
        )["items"]
        snapshot = services.repository.get_latest_ranking_snapshot(
            profile_id=profile_id
        )
        return templates.TemplateResponse(
            request=request,
            name="rankings.html",
            context=_context(
                request,
                page="rankings",
                title="热榜",
                snapshot=snapshot,
                profiles=profiles,
                selected_profile_id=profile_id,
                cover_display_mode=services.settings.get_public()[
                    "cover_display_mode"
                ],
            ),
        )

    @app.get("/watchlists", include_in_schema=False)
    def watchlists_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        return templates.TemplateResponse(
            request=request,
            name="watchlists.html",
            context=_context(
                request,
                page="watchlists",
                title="关注清单",
                watchlists=request.app.state.services.watchlists.list(),
            ),
        )

    @app.get("/watchlists/{watchlist_id}", include_in_schema=False)
    def watchlist_detail_page(request: Request, watchlist_id: int):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        services = request.app.state.services
        try:
            watchlist = services.watchlists.get(watchlist_id)
        except Exception:
            return RedirectResponse("/watchlists", status_code=303)
        profiles = services.profiles.list(
            page=1, page_size=100, include_archived=False
        )["items"]
        return templates.TemplateResponse(
            request=request,
            name="watchlist_detail.html",
            context=_context(
                request,
                page="watchlists",
                title=watchlist["name"],
                watchlist=watchlist,
                profiles=profiles,
                runtime_configured=services.runtime.configured,
                cover_display_mode=services.settings.get_public()[
                    "cover_display_mode"
                ],
            ),
        )

    @app.get("/profiles", include_in_schema=False)
    def profiles_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        result = request.app.state.services.profiles.list(
            page=1, page_size=100, include_archived=True
        )
        return templates.TemplateResponse(
            request=request,
            name="profiles.html",
            context=_context(
                request,
                page="profiles",
                title="订阅",
                profiles=result["items"],
            ),
        )

    @app.get("/profiles/new", include_in_schema=False)
    def new_profile_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        profile = {
            "id": None,
            "name": "",
            "destination_subdir": "Tuntu/weekly",
            "top_n": 25,
            "daily_time": "03:00",
            "enabled": False,
            "scope": "weekly",
            "discovery_sources": ["javdatabase_weekly", "javdb_ranking"],
            "candidate_sources": [
                "javdb_detail",
                "sukebei_rss",
                "knaben_api",
                "bitsearch_api",
            ],
            "rules": {},
            "auto_submit": False,
        }
        return _profile_form(request, profile, "新建订阅")

    @app.get("/profiles/{profile_id}", include_in_schema=False)
    def edit_profile_page(request: Request, profile_id: int):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        try:
            profile = request.app.state.services.profiles.get(profile_id)
        except Exception:
            return RedirectResponse("/profiles", status_code=303)
        return _profile_form(request, profile, "编辑订阅")

    def _profile_form(request: Request, profile: dict, title: str):
        return templates.TemplateResponse(
            request=request,
            name="profile_form.html",
            context=_context(
                request,
                page="profiles",
                title=title,
                profile=profile,
                catalog=request.app.state.services.profiles.catalog(),
            ),
        )

    @app.get("/runs", include_in_schema=False)
    def runs_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        runs, _ = request.app.state.services.repository.list_runs(limit=100)
        return templates.TemplateResponse(
            request=request,
            name="runs.html",
            context=_context(request, page="runs", title="运行记录", runs=runs),
        )

    @app.get("/runs/{run_id}", include_in_schema=False)
    def run_detail_page(request: Request, run_id: str):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        detail = request.app.state.services.repository.get_run_detail(run_id)
        if detail is None:
            return RedirectResponse("/runs", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context=_context(
                request, page="runs", title="运行详情", run=detail
            ),
        )

    @app.get("/manual", include_in_schema=False)
    def manual_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        profiles = request.app.state.services.profiles.list(
            page=1, page_size=100, include_archived=False
        )["items"]
        return templates.TemplateResponse(
            request=request,
            name="manual.html",
            context=_context(
                request,
                page="manual",
                title="手工任务",
                profiles=profiles,
                runtime_configured=request.app.state.services.runtime.configured,
            ),
        )

    @app.get("/downloads", include_in_schema=False)
    def downloads_page(request: Request, status: str | None = None):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        rows, _ = request.app.state.services.repository.list_downloads(
            limit=100, status=status
        )
        return templates.TemplateResponse(
            request=request,
            name="downloads.html",
            context=_context(
                request,
                page="downloads",
                title="下载记录",
                downloads=rows,
                status_filter=status,
            ),
        )

    @app.get("/downloads/{task_id}", include_in_schema=False)
    def download_detail_page(request: Request, task_id: str):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        detail = request.app.state.services.repository.get_download_detail(task_id)
        if detail is None:
            return RedirectResponse("/downloads", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="download_detail.html",
            context=_context(
                request,
                page="downloads",
                title="下载详情",
                download=detail,
                runtime_configured=request.app.state.services.runtime.configured,
            ),
        )

    @app.get("/sources", include_in_schema=False)
    def sources_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        return templates.TemplateResponse(
            request=request,
            name="sources.html",
            context=_context(
                request,
                page="sources",
                title="数据源",
                sources=request.app.state.services.runtime.source_catalog(),
                runtime_configured=request.app.state.services.runtime.configured,
            ),
        )

    @app.get("/settings", include_in_schema=False)
    def settings_page(request: Request):
        _current, redirect = protected(request)
        if redirect:
            return redirect
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_context(
                request,
                page="settings",
                title="系统设置",
                settings=request.app.state.services.settings.get_public(),
            ),
        )
