from __future__ import annotations

from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BeforeValidator, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _blank_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value


OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class StartupConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TUNTU_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    data_dir: Path = Path("/data")
    timezone: str = "Asia/Shanghai"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65_535)
    log_level: str = "INFO"
    max_concurrent_runs: int = Field(default=2, ge=1, le=16)
    allowed_hosts: str = "*"
    cookie_secure: bool = False
    setup_token_ttl_minutes: int = Field(default=30, ge=5, le=1_440)
    session_days: int = Field(default=7, ge=1, le=90)

    cd2_endpoint: OptionalText = None
    cd2_auth_mode: OptionalText = None
    cd2_api_token: OptionalText = None
    cd2_username: OptionalText = None
    cd2_password: OptionalText = None
    cd2_root: OptionalText = None
    cd2_test_subdir: OptionalText = None
    cd2_tls_verify: bool | None = None
    cd2_ca_certificate: OptionalText = None
    cd2_rpc_timeout_seconds: float | None = Field(default=None, gt=0)
    cd2_task_list_timeout_seconds: float | None = Field(default=None, gt=0)
    cd2_poll_interval_seconds: int | None = Field(default=None, gt=0)
    cd2_attention_after_hours: float | None = Field(default=None, gt=0)
    cd2_check_folder_after_seconds: int | None = Field(default=None, ge=0)
    cd2_required_stable_observations: int | None = Field(default=None, ge=2)
    cd2_max_tree_depth: int | None = Field(default=None, gt=0)
    cd2_max_tree_entries: int | None = Field(default=None, gt=0)

    outbound_proxy: OptionalText = None
    cover_display_mode: OptionalText = None
    javdb_base_url: OptionalText = None
    javdb_cookie: OptionalText = None
    javdb_user_agent: OptionalText = None
    javdatabase_feed_url: OptionalText = None
    sukebei_feed_url: OptionalText = None
    knaben_api_url: OptionalText = None
    bitsearch_api_url: OptionalText = None
    authorized_candidate_api_url: OptionalText = None
    authorized_candidate_api_token: OptionalText = None
    provider_timeout_seconds: float | None = Field(default=None, gt=0)
    provider_retries: int | None = Field(default=None, ge=0, le=10)
    provider_backoff_seconds: float | None = Field(default=None, ge=0)
    provider_cache_ttl_seconds: float | None = Field(default=None, ge=0)
    provider_min_interval_seconds: float | None = Field(default=None, ge=0)
    provider_max_response_bytes: int | None = Field(default=None, gt=0)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "tuntu.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def allowed_host_list(self) -> list[str]:
        values = [value.strip() for value in self.allowed_hosts.split(",")]
        return [value for value in values if value] or ["*"]

    def runtime_overrides(self) -> dict:
        mapping = {
            "timezone": self.timezone,
            "max_concurrent_runs": self.max_concurrent_runs,
            "outbound_proxy": self.outbound_proxy,
            "cover_display_mode": self.cover_display_mode,
            "javdb_base_url": self.javdb_base_url,
            "javdb_cookie": self.javdb_cookie,
            "javdb_user_agent": self.javdb_user_agent,
            "javdatabase_feed_url": self.javdatabase_feed_url,
            "sukebei_feed_url": self.sukebei_feed_url,
            "knaben_api_url": self.knaben_api_url,
            "bitsearch_api_url": self.bitsearch_api_url,
            "authorized_candidate_api_url": self.authorized_candidate_api_url,
            "authorized_candidate_api_token": self.authorized_candidate_api_token,
            "provider_timeout_seconds": self.provider_timeout_seconds,
            "provider_retries": self.provider_retries,
            "provider_backoff_seconds": self.provider_backoff_seconds,
            "provider_cache_ttl_seconds": self.provider_cache_ttl_seconds,
            "provider_min_interval_seconds": self.provider_min_interval_seconds,
            "provider_max_response_bytes": self.provider_max_response_bytes,
            "cd2_endpoint": self.cd2_endpoint,
            "cd2_auth_mode": self.cd2_auth_mode,
            "cd2_api_token": self.cd2_api_token,
            "cd2_username": self.cd2_username,
            "cd2_password": self.cd2_password,
            "cd2_root": self.cd2_root,
            "cd2_test_subdir": self.cd2_test_subdir,
            "cd2_tls_verify": self.cd2_tls_verify,
            "cd2_ca_certificate": self.cd2_ca_certificate,
            "cd2_rpc_timeout_seconds": self.cd2_rpc_timeout_seconds,
            "cd2_task_list_timeout_seconds": self.cd2_task_list_timeout_seconds,
            "cd2_poll_interval_seconds": self.cd2_poll_interval_seconds,
            "cd2_attention_after_hours": self.cd2_attention_after_hours,
            "cd2_check_folder_after_seconds": self.cd2_check_folder_after_seconds,
            "cd2_required_stable_observations": (
                self.cd2_required_stable_observations
            ),
            "cd2_max_tree_depth": self.cd2_max_tree_depth,
            "cd2_max_tree_entries": self.cd2_max_tree_entries,
        }
        return {
            key: value
            for key, value in mapping.items()
            if value is not None and key in self.model_fields_set
        }
