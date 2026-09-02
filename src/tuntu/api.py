from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from tuntu.auth import AuthError, AuthService, AuthenticatedUser
from tuntu import __version__
from tuntu.config import StartupConfig
from tuntu.db import DestinationBusy, IdempotencyConflict, Repository
from tuntu.db.database import Database, DatabaseLocked
from tuntu.db.migration import migrate_database
from tuntu.downloads.service import ConfirmationRequired
from tuntu.downloaders.clouddrive import CloudDriveError
from tuntu.manual import ManualError, ManualService
from tuntu.profiles import ProfileError, ProfileService
from tuntu.runtime import RuntimeManager, RuntimeUnavailable
from tuntu.settings import SettingsError, SettingsService
from tuntu.ui import install_ui
from tuntu.watchlists import WatchlistError, WatchlistService


LOGGER = logging.getLogger("tuntu")
SESSION_COOKIE = "tuntu_session"
CSRF_HEADER = "X-Tuntu-CSRF"


ERROR_MESSAGES = {
    "authentication_required": "请先登录。",
    "invalid_credentials": "用户名或密码错误。",
    "setup_token_invalid": "Setup Token 无效或已过期。",
    "csrf_required": "写请求缺少同源保护。",
    "profile_not_found": "订阅不存在。",
    "watchlist_not_found": "关注清单不存在。",
    "watchlist_item_not_found": "清单作品不存在。",
    "watchlist_automation_incomplete": "请先选择下载配置并填写每日执行时间。",
    "watchlist_requires_query_source": "下载配置至少需要一个可按标识查询的候选源。",
    "rights_confirmation_required": "请先确认你有权使用所填链接。",
    "metadata_only_required": "元数据导入不能包含磁力或种子下载链接。",
    "invalid_metadata_url": "元数据中的封面和出处必须是 HTTP(S) 地址。",
    "download_not_found": "下载记录不存在。",
    "run_not_found": "运行记录不存在。",
    "runtime_unavailable": "下载运行时尚未配置。",
    "cd2_not_configured": "请先配置 CloudDrive2。",
    "force_confirmation_required": "该操作需要二次确认。",
    "confirmation_required": "请确认后再提交。",
    "force_not_required": "当前没有重复记录，不需要强制提交。",
    "candidate_not_accepted": "候选不存在、未通过筛选或不属于本次手工查询。",
    "invalid_request": "请求参数不正确。",
    "internal_error": "服务暂时无法处理该请求。",
}


def api_error(code: str, *, status_code: int = 400, message: str | None = None):
    return JSONResponse(
        {"error": {"code": code, "message": message or ERROR_MESSAGES.get(code, code)}},
        status_code=status_code,
    )


def require_user(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> AuthenticatedUser:
    user = request.app.state.services.auth.authenticate(session_token)
    request.state.session_token_to_refresh = session_token
    return user


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupInput(StrictModel):
    token: str = Field(min_length=20, max_length=256)
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=12, max_length=256)


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class PasswordInput(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class RunInput(StrictModel):
    force_dry_run: bool = False


class RetryInput(StrictModel):
    confirmed: bool = False


class ManualCompleteInput(StrictModel):
    confirmed: bool = False


class SourceProbeInput(StrictModel):
    query: str | None = Field(default=None, max_length=100)
    scope: str = Field(default="weekly", max_length=20)


class SourceEnabledInput(StrictModel):
    enabled: bool


class WatchlistCreateInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    subject_type: str = Field(max_length=30)
    query: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class WatchlistImportItemInput(StrictModel):
    namespace: str = Field(default="general", min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=300)
    title: str = Field(default="", max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WatchlistImportInput(StrictModel):
    source_name: str = Field(min_length=1, max_length=100)
    items: list[WatchlistImportItemInput] = Field(min_length=1, max_length=500)


class WatchlistItemStateInput(StrictModel):
    state: str = Field(max_length=30)


class WatchlistAutomationInput(StrictModel):
    profile_id: int | None = Field(default=None, gt=0)
    daily_time: str | None = Field(default=None, max_length=8)
    enabled: bool = False
    auto_submit: bool = False
    rights_confirmed: bool = False


class WatchlistMagnetInput(StrictModel):
    profile_id: int = Field(gt=0)
    magnet_uri: str = Field(min_length=1, max_length=8_000)
    rights_confirmed: bool = False
    confirmed: bool = False


class NumberPreviewInput(StrictModel):
    profile_id: int = Field(gt=0)
    number: str = Field(min_length=1, max_length=100)


class NumberSubmitInput(StrictModel):
    profile_id: int = Field(gt=0)
    run_id: str = Field(min_length=36, max_length=36)
    candidate_id: int = Field(gt=0)
    confirmed: bool = False


class MagnetInput(StrictModel):
    profile_id: int = Field(gt=0)
    magnet_uri: str = Field(min_length=1, max_length=8_000)
    title: str = Field(default="", max_length=500)


class MagnetSubmitInput(MagnetInput):
    force: bool = False
    confirmed: bool = False


@dataclass(slots=True)
class ApplicationServices:
    config: StartupConfig
    database: Database
    repository: Repository
    auth: AuthService
    settings: SettingsService
    runtime: RuntimeManager
    profiles: ProfileService
    watchlists: WatchlistService
    manual: ManualService
    owns_database: bool = True


def build_services(config: StartupConfig, *, start_scheduler: bool = True):
    migrate_database(config.database_path, config.backup_dir)
    database = Database(config.database_path)
    repository = Repository(database)
    settings = SettingsService(
        repository, environment_overrides=config.runtime_overrides()
    )
    runtime = RuntimeManager(
        repository, settings, start_scheduler=start_scheduler
    )
    profiles = ProfileService(repository, scheduler_sync=runtime.sync_scheduler)
    manual = ManualService(repository, runtime)
    watchlists = WatchlistService(
        repository,
        manual_service=manual,
        runtime=runtime,
        scheduler_sync=runtime.sync_scheduler,
    )
    runtime.watchlist_runner = watchlists
    return ApplicationServices(
        config=config,
        database=database,
        repository=repository,
        auth=AuthService(
            database,
            setup_token_ttl=timedelta(minutes=config.setup_token_ttl_minutes),
            session_ttl=timedelta(days=config.session_days),
        ),
        settings=settings,
        runtime=runtime,
        profiles=profiles,
        watchlists=watchlists,
        manual=manual,
    )


def create_app(
    config: StartupConfig | None = None,
    *,
    services: ApplicationServices | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    config = config or StartupConfig()
    services = services or build_services(config, start_scheduler=start_scheduler)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        grant = services.auth.bootstrap_if_needed()
        if grant is not None:
            LOGGER.warning(
                "首次初始化 Setup Token（%s 前有效）：%s",
                grant.expires_at.isoformat(),
                grant.token,
            )
        services.runtime.reload()
        yield
        services.runtime.close()
        if services.owns_database:
            services.database.dispose()

    app = FastAPI(
        title="Tuntu",
        version=__version__,
        description="Tuntu 自托管管理接口；v0.1 不承诺第三方兼容稳定性。",
        lifespan=lifespan,
    )
    app.state.services = services
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=config.allowed_host_list
    )

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            has_custom_header = request.headers.get(CSRF_HEADER) == "1"
            if origin != expected and not has_custom_header:
                return api_error("csrf_required", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: http: https:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        session_token = getattr(request.state, "session_token_to_refresh", None)
        if session_token and not getattr(request.state, "clear_session_cookie", False):
            set_session_cookie(response, session_token, services.config)
        return response

    install_error_handlers(app)
    install_routes(app)
    install_ui(app)
    return app


def install_error_handlers(app: FastAPI) -> None:
    domain_errors = (
        AuthError,
        ProfileError,
        WatchlistError,
        ManualError,
        SettingsError,
        RuntimeUnavailable,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        return api_error("invalid_request", status_code=422)

    @app.exception_handler(AuthError)
    async def auth_error(_request, exc):
        status = 401 if exc.code in {"authentication_required", "invalid_credentials"} else 400
        return api_error(exc.code, status_code=status)

    for error_type in domain_errors[1:]:
        app.add_exception_handler(
            error_type,
            lambda _request, exc: api_error(exc.code, status_code=400),
        )

    @app.exception_handler(ConfirmationRequired)
    async def confirmation_error(_request, _exc):
        return api_error("force_confirmation_required", status_code=409)

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_error(_request, _exc):
        return api_error("duplicate_download", status_code=409)

    @app.exception_handler(DestinationBusy)
    async def destination_error(_request, _exc):
        return api_error("destination_busy", status_code=409)

    @app.exception_handler(DatabaseLocked)
    async def database_locked(_request, _exc):
        return api_error("database_busy", status_code=503)

    @app.exception_handler(CloudDriveError)
    async def clouddrive_error(_request, exc):
        return api_error(exc.code, status_code=502)

    @app.exception_handler(KeyError)
    async def key_error(_request, _exc):
        return api_error("not_found", status_code=404)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        LOGGER.error(
            "未处理请求异常 method=%s path=%s error_type=%s",
            request.method,
            request.url.path,
            type(exc).__name__,
        )
        return api_error("internal_error", status_code=500)


def install_routes(app: FastAPI) -> None:
    services: ApplicationServices = app.state.services

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/ready", include_in_schema=False)
    def ready():
        try:
            with services.database.session() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            return api_error("database_unavailable", status_code=503)
        return {
            "status": "ready",
            "runtime_configured": services.runtime.configured,
            "runtime_error": services.runtime.error_code,
        }

    public = APIRouter(prefix="/api/v1/auth", tags=["认证"])

    @public.get("/status")
    def auth_status():
        return asdict(services.auth.bootstrap_status())

    @public.post("/setup")
    def setup(payload: SetupInput, response: Response):
        user = services.auth.consume_setup_token(
            payload.token, payload.username, payload.password
        )
        grant = services.auth.login(user.username, payload.password)
        set_session_cookie(response, grant.token, services.config)
        services.repository.add_audit_event(
            "admin_initialized", entity_type="user", entity_id=str(user.id)
        )
        return {"user": asdict(user)}

    @public.post("/login")
    def login(payload: LoginInput, response: Response):
        grant = services.auth.login(payload.username, payload.password)
        set_session_cookie(response, grant.token, services.config)
        return {"authenticated": True}

    app.include_router(public)

    api = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_user)],
    )

    @api.get("/auth/me", tags=["认证"])
    def me(user: Annotated[AuthenticatedUser, Depends(require_user)]):
        return asdict(user)

    @api.post("/auth/logout", tags=["认证"])
    def logout(
        request: Request,
        response: Response,
        session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ):
        services.auth.logout(session_token)
        request.state.clear_session_cookie = True
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"authenticated": False}

    @api.post("/auth/password", tags=["认证"])
    def change_password(
        payload: PasswordInput,
        request: Request,
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(require_user)],
        session_token: Annotated[str, Cookie(alias=SESSION_COOKIE)],
    ):
        services.auth.change_password(
            session_token, payload.current_password, payload.new_password
        )
        request.state.clear_session_cookie = True
        response.delete_cookie(SESSION_COOKIE, path="/")
        services.repository.add_audit_event(
            "admin_password_changed", entity_type="user", entity_id=str(user.id)
        )
        return {"authenticated": False}

    @api.get("/settings", tags=["设置"])
    def get_settings():
        return services.settings.get_public()

    @api.put("/settings", tags=["设置"])
    def update_settings(payload: Annotated[dict[str, Any], Body()]):
        result = services.settings.update(payload)
        services.repository.add_audit_event("settings_updated")
        services.runtime.reload()
        return result

    @api.post("/settings/clouddrive/test", tags=["设置"])
    async def test_clouddrive():
        result = await run_in_threadpool(services.settings.test_clouddrive)
        services.repository.add_audit_event("clouddrive_connection_tested")
        return asdict(result)

    @api.get("/profiles/catalog", tags=["订阅"])
    def profile_catalog():
        return services.profiles.catalog()

    @api.get("/profiles", tags=["订阅"])
    def list_profiles(
        page: Annotated[int, Query(ge=1, le=100_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        include_archived: bool = False,
    ):
        return services.profiles.list(
            page=page, page_size=page_size, include_archived=include_archived
        )

    @api.post("/profiles", tags=["订阅"], status_code=201)
    def create_profile(payload: Annotated[dict[str, Any], Body()]):
        return services.profiles.create(payload)

    @api.get("/profiles/{profile_id}", tags=["订阅"])
    def get_profile(profile_id: int):
        return services.profiles.get(profile_id)

    @api.put("/profiles/{profile_id}", tags=["订阅"])
    def update_profile(profile_id: int, payload: Annotated[dict[str, Any], Body()]):
        return services.profiles.update(profile_id, payload)

    @api.post("/profiles/{profile_id}/archive", tags=["订阅"])
    def archive_profile(profile_id: int):
        return services.profiles.archive(profile_id)

    @api.post("/profiles/{profile_id}/restore", tags=["订阅"])
    def restore_profile(profile_id: int):
        return services.profiles.restore(profile_id)

    @api.post("/profiles/{profile_id}/run", tags=["订阅"])
    async def run_profile(profile_id: int, payload: RunInput):
        result = await run_in_threadpool(
            services.runtime.execute_profile,
            profile_id,
            force_dry_run=payload.force_dry_run,
        )
        return asdict(result)

    @api.get("/watchlists", tags=["关注清单"])
    def list_watchlists():
        return {"items": services.watchlists.list()}

    @api.post("/watchlists", tags=["关注清单"], status_code=201)
    def create_watchlist(payload: WatchlistCreateInput):
        return services.watchlists.create(payload.model_dump())

    @api.get("/watchlists/{watchlist_id}", tags=["关注清单"])
    def get_watchlist(watchlist_id: int):
        return services.watchlists.get(watchlist_id)

    @api.put("/watchlists/{watchlist_id}/automation", tags=["关注清单"])
    def configure_watchlist_automation(
        watchlist_id: int, payload: WatchlistAutomationInput
    ):
        return services.watchlists.configure_automation(
            watchlist_id, payload.model_dump()
        )

    @api.post("/watchlists/{watchlist_id}/run", tags=["关注清单"])
    async def run_watchlist(watchlist_id: int, payload: RunInput):
        result = await run_in_threadpool(
            services.watchlists.run,
            watchlist_id,
            force_dry_run=payload.force_dry_run,
            trigger="manual",
        )
        return asdict(result)

    @api.post("/watchlists/{watchlist_id}/items/import", tags=["关注清单"])
    def import_watchlist_items(watchlist_id: int, payload: WatchlistImportInput):
        return services.watchlists.import_items(
            watchlist_id,
            source_name=payload.source_name,
            items=[item.model_dump() for item in payload.items],
        )

    @api.patch(
        "/watchlists/{watchlist_id}/items/{content_item_id}", tags=["关注清单"]
    )
    def update_watchlist_item(
        watchlist_id: int,
        content_item_id: int,
        payload: WatchlistItemStateInput,
    ):
        return services.watchlists.set_item_state(
            watchlist_id, content_item_id, payload.state
        )

    @api.post(
        "/watchlists/{watchlist_id}/items/{content_item_id}/authorized-magnet",
        tags=["关注清单"],
    )
    async def submit_watchlist_magnet(
        watchlist_id: int,
        content_item_id: int,
        payload: WatchlistMagnetInput,
    ):
        return await run_in_threadpool(
            services.watchlists.submit_authorized_magnet,
            watchlist_id,
            content_item_id,
            profile_id=payload.profile_id,
            magnet_uri=payload.magnet_uri,
            rights_confirmed=payload.rights_confirmed,
            confirmed=payload.confirmed,
        )

    @api.post("/manual/number/preview", tags=["手工"])
    async def number_preview(payload: NumberPreviewInput):
        return await run_in_threadpool(
            services.manual.number_preview, payload.profile_id, payload.number
        )

    @api.post("/manual/number/submit", tags=["手工"])
    async def number_submit(payload: NumberSubmitInput):
        return await run_in_threadpool(
            services.manual.submit_number_candidate,
            payload.profile_id,
            run_id=payload.run_id,
            candidate_id=payload.candidate_id,
            confirmed=payload.confirmed,
        )

    @api.post("/manual/magnet/preview", tags=["手工"])
    def magnet_preview(payload: MagnetInput):
        return services.manual.magnet_preview(
            payload.profile_id, payload.magnet_uri, payload.title
        )

    @api.post("/manual/magnet/submit", tags=["手工"])
    async def magnet_submit(payload: MagnetSubmitInput):
        return await run_in_threadpool(
            services.manual.submit_magnet,
            payload.profile_id,
            payload.magnet_uri,
            title=payload.title,
            force=payload.force,
            confirmed=payload.confirmed,
        )

    @api.get("/runs", tags=["运行"])
    def list_runs(
        page: Annotated[int, Query(ge=1, le=100_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        profile_id: int | None = None,
    ):
        rows, total = services.repository.list_runs(
            offset=(page - 1) * page_size,
            limit=page_size,
            profile_id=profile_id,
        )
        return {"items": rows, "page": page, "page_size": page_size, "total": total}

    @api.get("/runs/{run_id}", tags=["运行"])
    def run_detail(run_id: str):
        detail = services.repository.get_run_detail(run_id)
        if detail is None:
            raise RuntimeUnavailable("run_not_found")
        return detail

    @api.get("/downloads", tags=["下载"])
    def list_downloads(
        page: Annotated[int, Query(ge=1, le=100_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        status: Annotated[str | None, Query(max_length=30)] = None,
    ):
        rows, total = services.repository.list_downloads(
            offset=(page - 1) * page_size, limit=page_size, status=status
        )
        return {"items": rows, "page": page, "page_size": page_size, "total": total}

    @api.get("/downloads/{task_id}", tags=["下载"])
    def download_detail(task_id: str):
        detail = services.repository.get_download_detail(task_id)
        if detail is None:
            raise RuntimeUnavailable("download_not_found")
        return detail

    @api.post("/downloads/{task_id}/retry", tags=["下载"])
    async def retry_download(task_id: str, payload: RetryInput):
        runtime = services.runtime.require()
        retry_id = await run_in_threadpool(
            runtime.download_service.retry, task_id, confirmed=payload.confirmed
        )
        return services.repository.get_download_detail(retry_id)

    @api.post("/downloads/{task_id}/manual-complete", tags=["下载"])
    def complete_download(task_id: str, payload: ManualCompleteInput):
        runtime = services.runtime.require()
        runtime.download_service.manual_complete(task_id, confirmed=payload.confirmed)
        return services.repository.get_download_detail(task_id)

    @api.get("/sources", tags=["来源"])
    def list_sources():
        return {"items": services.runtime.source_catalog()}

    @api.post("/sources/{name}/test", tags=["来源"])
    async def test_source(name: str, payload: SourceProbeInput):
        return await run_in_threadpool(
            services.runtime.probe_source,
            name,
            query=payload.query,
            scope=payload.scope,
        )

    @api.put("/sources/{name}", tags=["来源"])
    def set_source_enabled(name: str, payload: SourceEnabledInput):
        result = services.runtime.set_source_enabled(name, payload.enabled)
        services.repository.add_audit_event(
            "source_enabled" if payload.enabled else "source_disabled",
            entity_type="source",
            entity_id=name,
        )
        return result

    @api.get("/dashboard", tags=["仪表盘"])
    def dashboard():
        recent_runs, _ = services.repository.list_runs(limit=5)
        downloads, _ = services.repository.list_downloads(limit=5)
        return {
            "summary": services.repository.dashboard_summary(),
            "recent_runs": recent_runs,
            "recent_downloads": downloads,
            "sources": services.runtime.source_catalog(),
        }

    @api.get("/rankings", tags=["热榜"])
    def rankings(profile_id: int | None = None):
        return {
            "cover_display_mode": services.settings.get_public()[
                "cover_display_mode"
            ],
            "snapshot": services.repository.get_latest_ranking_snapshot(
                profile_id=profile_id
            ),
        }

    @api.get("/exports/downloads.csv", tags=["导出"])
    def export_downloads():
        stream = io.StringIO()
        writer = csv.writer(stream)
        columns = [
            "内容标识",
            "标题",
            "榜单来源",
            "排名",
            "BTIH",
            "候选来源",
            "筛选结果",
            "Profile",
            "目标目录",
            "状态",
            "创建时间",
            "更新时间",
        ]
        writer.writerow(columns)
        for row in services.repository.download_export_rows():
            writer.writerow(
                [
                    csv_safe(row["content_key"]),
                    csv_safe(row["title"]),
                    csv_safe(row.get("ranking_sources", "")),
                    csv_safe(row.get("rank", "")),
                    csv_safe(row["btih"]),
                    csv_safe(row.get("candidate_sources", "")),
                    csv_safe(row.get("evaluation", "")),
                    csv_safe(row["profile_name"]),
                    csv_safe(row["destination_path"] or ""),
                    csv_safe(row["status"]),
                    row["created_at"].isoformat(),
                    row["updated_at"].isoformat(),
                ]
            )
        payload = ("\ufeff" + stream.getvalue()).encode("utf-8")
        return StreamingResponse(
            iter([payload]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="tuntu-downloads.csv"'},
        )

    app.include_router(api)


def set_session_cookie(response: Response, token: str, config: StartupConfig) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.session_days * 86_400,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def csv_safe(value: Any) -> str:
    text_value = str(value)
    if text_value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text_value
    return text_value
