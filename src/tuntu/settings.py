from __future__ import annotations

import copy
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tuntu.downloaders.clouddrive import (
    AuthMode,
    CloudDriveClient,
    CloudDriveConfig,
    CloudDriveConfigurationError,
    resolve_destination,
)
from tuntu.profiles import CANDIDATE_SOURCES, DISCOVERY_SOURCES


SOURCE_NAMES = frozenset(DISCOVERY_SOURCES) | frozenset(CANDIDATE_SOURCES)


class SettingsError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    product_name: str
    product_version: str
    api_version: str
    test_destination: str
    existing_file_count: int


class SettingsService:
    SETTING_KEY = "runtime"
    SECRET_FIELDS = {
        "cd2_api_token",
        "cd2_password",
        "cd2_ca_certificate",
        "javdb_cookie",
        "authorized_candidate_api_token",
    }
    DEFAULTS = {
        "timezone": "Asia/Shanghai",
        "max_concurrent_runs": 2,
        "disabled_sources": [],
        "cover_display_mode": "blur",
        "outbound_proxy": None,
        "javdb_base_url": "https://javdb.com",
        "javdb_cookie": None,
        "javdb_user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "javdatabase_feed_url": "https://www.javdatabase.com/category/top-jav-movies/feed/",
        "sukebei_feed_url": "https://sukebei.nyaa.si/",
        "knaben_api_url": "https://api.knaben.org/v1",
        "bitsearch_api_url": "https://bitsearch.to/api/v1/search",
        "authorized_candidate_api_url": None,
        "authorized_candidate_api_token": None,
        "provider_timeout_seconds": 15.0,
        "provider_retries": 2,
        "provider_backoff_seconds": 0.25,
        "provider_cache_ttl_seconds": 300.0,
        "provider_min_interval_seconds": 1.0,
        "provider_max_response_bytes": 3_000_000,
        "cd2_endpoint": None,
        "cd2_auth_mode": AuthMode.USER_PASSWORD.value,
        "cd2_api_token": None,
        "cd2_username": None,
        "cd2_password": None,
        "cd2_root": "/",
        "cd2_test_subdir": ".tuntu-test",
        "cd2_tls_verify": True,
        "cd2_ca_certificate": None,
        "cd2_rpc_timeout_seconds": 8.0,
        "cd2_task_list_timeout_seconds": 8.0,
        "cd2_poll_interval_seconds": 300,
        "cd2_attention_after_hours": 24.0,
        "cd2_check_folder_after_seconds": 5,
        "cd2_required_stable_observations": 2,
        "cd2_max_tree_depth": 8,
        "cd2_max_tree_entries": 10_000,
    }

    def __init__(self, repository, *, environment_overrides=None, client_factory=None):
        self.repository = repository
        self.environment_overrides = copy.deepcopy(environment_overrides or {})
        self._client_factory = client_factory or CloudDriveClient
        unknown = set(self.environment_overrides) - set(self.DEFAULTS)
        if unknown:
            raise SettingsError("unknown_environment_override")

    def get_stored(self) -> dict:
        return self.repository.get_setting(self.SETTING_KEY) or {}

    def get_effective(self) -> dict:
        effective = copy.deepcopy(self.DEFAULTS)
        effective.update(self.get_stored())
        effective.update(self.environment_overrides)
        self._validate(effective, allow_unconfigured=True)
        return effective

    def get_public(self) -> dict:
        effective = self.get_effective()
        public = {
            key: value
            for key, value in effective.items()
            if key not in self.SECRET_FIELDS
        }
        for field in self.SECRET_FIELDS:
            public[f"{field}_configured"] = bool(effective.get(field))
        public["environment_overrides"] = sorted(self.environment_overrides)
        return public

    def update(self, changes: dict) -> dict:
        unknown = set(changes) - set(self.DEFAULTS)
        if unknown:
            raise SettingsError("unknown_setting")
        stored = self.get_stored()
        candidate = copy.deepcopy(self.DEFAULTS)
        candidate.update(stored)
        candidate.update(changes)
        self._validate(candidate, allow_unconfigured=True)
        stored.update(copy.deepcopy(changes))
        self.repository.set_setting(self.SETTING_KEY, stored)
        return self.get_public()

    def build_clouddrive_config(self) -> CloudDriveConfig:
        values = self.get_effective()
        self._validate(values, allow_unconfigured=False)
        ca_value = values["cd2_ca_certificate"]
        return CloudDriveConfig(
            endpoint=values["cd2_endpoint"],
            auth_mode=AuthMode(values["cd2_auth_mode"]),
            root_path=values["cd2_root"],
            api_token=values["cd2_api_token"] or "",
            username=values["cd2_username"] or "",
            password=values["cd2_password"] or "",
            tls_verify=values["cd2_tls_verify"],
            ca_certificate_pem=(
                ca_value.encode("utf-8") if ca_value else None
            ),
            rpc_timeout_seconds=values["cd2_rpc_timeout_seconds"],
            task_list_timeout_seconds=values["cd2_task_list_timeout_seconds"],
            poll_interval_seconds=values["cd2_poll_interval_seconds"],
            attention_after_seconds=round(
                values["cd2_attention_after_hours"] * 3_600
            ),
            check_folder_after_seconds=values[
                "cd2_check_folder_after_seconds"
            ],
            required_stable_observations=values[
                "cd2_required_stable_observations"
            ],
            max_tree_depth=values["cd2_max_tree_depth"],
            max_tree_entries=values["cd2_max_tree_entries"],
        )

    def test_clouddrive(self) -> ConnectionTestResult:
        values = self.get_effective()
        config = self.build_clouddrive_config()
        client = self._client_factory(config)
        try:
            health = client.health_check()
            destination = resolve_destination(
                config.root_path, values["cd2_test_subdir"]
            )
            client.ensure_destination(destination)
            snapshot = client.snapshot(destination, force_refresh=True)
            return ConnectionTestResult(
                health.product_name,
                health.product_version,
                health.api_version,
                destination,
                snapshot.file_count,
            )
        finally:
            client.close()

    @classmethod
    def _validate(cls, values: dict, *, allow_unconfigured: bool) -> None:
        try:
            ZoneInfo(values["timezone"])
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise SettingsError("invalid_timezone") from exc
        cls._require_range(values, "max_concurrent_runs", 1, 16)
        cls._require_range(values, "provider_timeout_seconds", 0.001, None)
        cls._require_range(values, "provider_retries", 0, 10)
        cls._require_range(values, "provider_backoff_seconds", 0, None)
        cls._require_range(values, "provider_cache_ttl_seconds", 0, None)
        cls._require_range(values, "provider_min_interval_seconds", 0, None)
        cls._require_range(values, "provider_max_response_bytes", 1, None)
        disabled_sources = values.get("disabled_sources")
        if (
            not isinstance(disabled_sources, list)
            or len(disabled_sources) > len(SOURCE_NAMES)
            or any(
                not isinstance(value, str) or value not in SOURCE_NAMES
                for value in disabled_sources
            )
        ):
            raise SettingsError("invalid_disabled_sources")
        if values.get("cover_display_mode") not in {"none", "blur", "normal"}:
            raise SettingsError("invalid_cover_display_mode")
        cls._require_range(values, "cd2_rpc_timeout_seconds", 0.001, None)
        cls._require_range(values, "cd2_task_list_timeout_seconds", 0.001, None)
        cls._require_range(values, "cd2_poll_interval_seconds", 1, None)
        cls._require_range(values, "cd2_attention_after_hours", 0.001, None)
        cls._require_range(values, "cd2_check_folder_after_seconds", 0, None)
        cls._require_range(values, "cd2_required_stable_observations", 2, None)
        cls._require_range(values, "cd2_max_tree_depth", 1, None)
        cls._require_range(values, "cd2_max_tree_entries", 1, None)
        cls._require_text(values, "timezone", 100, optional=False)
        cls._require_text(values, "outbound_proxy", 1_000)
        for key in (
            "javdb_base_url",
            "javdatabase_feed_url",
            "sukebei_feed_url",
            "knaben_api_url",
            "bitsearch_api_url",
        ):
            cls._require_text(values, key, 1_000, optional=False)
            cls._require_http_url(values, key)
        cls._require_text(values, "javdb_cookie", 16_000)
        cls._require_text(values, "javdb_user_agent", 1_000, optional=False)
        cls._require_text(values, "authorized_candidate_api_url", 1_000)
        if values.get("authorized_candidate_api_url") is not None:
            cls._require_http_url(values, "authorized_candidate_api_url")
        cls._require_text(values, "authorized_candidate_api_token", 4_000)
        cls._require_text(values, "cd2_endpoint", 1_000)
        cls._require_text(values, "cd2_api_token", 4_000)
        cls._require_text(values, "cd2_username", 500)
        cls._require_text(values, "cd2_password", 4_000)
        cls._require_text(values, "cd2_root", 1_000, optional=False)
        cls._require_text(values, "cd2_test_subdir", 500, optional=False)
        cls._require_text(values, "cd2_ca_certificate", 20_000)
        if not isinstance(values.get("cd2_tls_verify"), bool):
            raise SettingsError("invalid_cd2_tls_verify")
        try:
            AuthMode(values["cd2_auth_mode"])
            resolve_destination(values["cd2_root"], values["cd2_test_subdir"])
        except (CloudDriveConfigurationError, ValueError, TypeError) as exc:
            raise SettingsError("invalid_clouddrive_settings") from exc

        endpoint = values.get("cd2_endpoint")
        if not endpoint:
            if allow_unconfigured:
                return
            raise SettingsError("cd2_not_configured")
        try:
            config = CloudDriveConfig(
                endpoint=endpoint,
                auth_mode=AuthMode(values["cd2_auth_mode"]),
                root_path=values["cd2_root"],
                api_token=values.get("cd2_api_token") or "",
                username=values.get("cd2_username") or "",
                password=values.get("cd2_password") or "",
                tls_verify=values["cd2_tls_verify"],
                rpc_timeout_seconds=values["cd2_rpc_timeout_seconds"],
                task_list_timeout_seconds=values[
                    "cd2_task_list_timeout_seconds"
                ],
                poll_interval_seconds=values["cd2_poll_interval_seconds"],
                attention_after_seconds=round(
                    values["cd2_attention_after_hours"] * 3_600
                ),
                check_folder_after_seconds=values[
                    "cd2_check_folder_after_seconds"
                ],
                required_stable_observations=values[
                    "cd2_required_stable_observations"
                ],
                max_tree_depth=values["cd2_max_tree_depth"],
                max_tree_entries=values["cd2_max_tree_entries"],
            )
            resolve_destination(config.root_path, values["cd2_test_subdir"])
        except (CloudDriveConfigurationError, ValueError, TypeError) as exc:
            raise SettingsError("invalid_clouddrive_settings") from exc

    @staticmethod
    def _require_range(values, key, minimum, maximum) -> None:
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"invalid_{key}")
        if value < minimum or (maximum is not None and value > maximum):
            raise SettingsError(f"invalid_{key}")

    @staticmethod
    def _require_text(values, key, maximum, *, optional=True) -> None:
        value = values.get(key)
        if value is None and optional:
            return
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise SettingsError(f"invalid_{key}")

    @staticmethod
    def _require_http_url(values, key) -> None:
        value = values.get(key)
        try:
            parsed = urlsplit(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"invalid_{key}") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SettingsError(f"invalid_{key}")
