from __future__ import annotations

import copy
import re
from dataclasses import asdict

from tuntu.downloaders.clouddrive import CloudDriveConfigurationError, resolve_destination
from tuntu.rules import RuleMode, RuleSet


DISCOVERY_SOURCES = {
    "javdb_ranking": {
        "name": "javdb_ranking",
        "label": "JavDB 网页榜单",
        "kind": "discovery",
        "scopes": ["daily", "weekly", "monthly"],
        "probe_mode": "scope",
    },
    "javdatabase_weekly": {
        "name": "javdatabase_weekly",
        "label": "JavDatabase 周榜",
        "kind": "discovery",
        "scopes": ["weekly"],
        "probe_mode": "scope",
    },
}

CANDIDATE_SOURCES = {
    "authorized_json_api": {
        "name": "authorized_json_api",
        "label": "自有 / 授权 JSON API",
        "kind": "candidate",
        "probe_mode": "query",
    },
    "javdb_detail": {
        "name": "javdb_detail",
        "label": "JavDB 网页磁力",
        "kind": "candidate",
        "probe_mode": "query",
    },
    "sukebei_rss": {
        "name": "sukebei_rss",
        "label": "Sukebei RSS",
        "kind": "candidate",
        "probe_mode": "query",
    },
    "knaben_api": {
        "name": "knaben_api",
        "label": "Knaben API",
        "kind": "candidate",
        "probe_mode": "query",
    },
    "bitsearch_api": {
        "name": "bitsearch_api",
        "label": "Bitsearch API",
        "kind": "candidate",
        "probe_mode": "query",
    },
}

_DAILY_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")


class ProfileError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProfileService:
    INPUT_FIELDS = frozenset(
        {
            "name",
            "destination_subdir",
            "top_n",
            "daily_time",
            "enabled",
            "scope",
            "discovery_sources",
            "candidate_sources",
            "rules",
            "auto_submit",
        }
    )

    def __init__(self, repository, *, scheduler_sync=None):
        self.repository = repository
        self._scheduler_sync = scheduler_sync or (lambda: None)

    @staticmethod
    def catalog() -> dict:
        return {
            "discovery": list(DISCOVERY_SOURCES.values()),
            "candidate": list(CANDIDATE_SOURCES.values()),
            "scopes": ["daily", "weekly", "monthly"],
        }

    def list(self, *, page: int, page_size: int, include_archived: bool) -> dict:
        rows, total = self.repository.list_profiles(
            offset=(page - 1) * page_size,
            limit=page_size,
            include_archived=include_archived,
        )
        return {
            "items": [self.serialize(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def get(self, profile_id: int) -> dict:
        row = self.repository.get_profile(profile_id)
        if row is None:
            raise ProfileError("profile_not_found")
        return self.serialize(row)

    def create(self, payload: dict) -> dict:
        self._require_known_fields(payload)
        values = self._validate(payload)
        profile_id = self.repository.create_profile(**values)
        self.repository.add_audit_event(
            "profile_created", entity_type="profile", entity_id=str(profile_id)
        )
        self._scheduler_sync()
        return self.get(profile_id)

    def update(self, profile_id: int, payload: dict) -> dict:
        existing = self.repository.get_profile(profile_id)
        if existing is None:
            raise ProfileError("profile_not_found")
        self._require_known_fields(payload)
        merged = self.serialize(existing)
        merged.update(copy.deepcopy(payload))
        values = self._validate(merged)
        try:
            self.repository.update_profile(profile_id, **values)
        except KeyError as exc:
            raise ProfileError("profile_not_found") from exc
        self.repository.add_audit_event(
            "profile_updated", entity_type="profile", entity_id=str(profile_id)
        )
        self._scheduler_sync()
        return self.get(profile_id)

    def archive(self, profile_id: int) -> dict:
        try:
            self.repository.archive_profile(profile_id)
        except KeyError as exc:
            raise ProfileError("profile_not_found") from exc
        self.repository.add_audit_event(
            "profile_archived", entity_type="profile", entity_id=str(profile_id)
        )
        self._scheduler_sync()
        return self.get(profile_id)

    def restore(self, profile_id: int) -> dict:
        try:
            self.repository.restore_profile(profile_id)
        except KeyError as exc:
            raise ProfileError("profile_not_found") from exc
        self.repository.add_audit_event(
            "profile_restored", entity_type="profile", entity_id=str(profile_id)
        )
        self._scheduler_sync()
        return self.get(profile_id)

    @staticmethod
    def serialize(row) -> dict:
        candidate_sources = list(row.settings.get("candidate_sources", []))
        return {
            "id": row.id,
            "name": row.name,
            "destination_subdir": row.destination_subdir,
            "top_n": row.top_n,
            "daily_time": row.daily_time,
            "enabled": row.enabled,
            "archived_at": row.archived_at,
            "scope": row.settings.get("scope", "weekly"),
            "discovery_sources": list(row.settings.get("discovery_sources", [])),
            "candidate_sources": candidate_sources,
            "watchlist_compatible": any(
                CANDIDATE_SOURCES.get(name, {}).get("probe_mode") == "query"
                for name in candidate_sources
            ),
            "rules": copy.deepcopy(row.settings.get("rules", {})),
            "auto_submit": bool(row.settings.get("auto_submit", False)),
        }

    @classmethod
    def _require_known_fields(cls, payload: dict) -> None:
        if not isinstance(payload, dict) or set(payload) - cls.INPUT_FIELDS:
            raise ProfileError("invalid_request")

    @classmethod
    def _validate(cls, payload: dict) -> dict:
        name = payload.get("name")
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 200):
            raise ProfileError("invalid_profile_name")
        destination = payload.get("destination_subdir")
        if not isinstance(destination, str) or len(destination) > 500:
            raise ProfileError("invalid_destination_subdir")
        try:
            resolve_destination("/", destination)
        except CloudDriveConfigurationError as exc:
            raise ProfileError("invalid_destination_subdir") from exc
        top_n = payload.get("top_n", 20)
        if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 100:
            raise ProfileError("invalid_top_n")
        daily_time = payload.get("daily_time") or None
        if daily_time is not None and (
            not isinstance(daily_time, str) or not _DAILY_TIME.fullmatch(daily_time)
        ):
            raise ProfileError("invalid_daily_time")
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ProfileError("invalid_enabled")
        auto_submit = payload.get("auto_submit", False)
        if not isinstance(auto_submit, bool):
            raise ProfileError("invalid_auto_submit")
        scope = payload.get("scope", "weekly")
        if scope not in {"daily", "weekly", "monthly"}:
            raise ProfileError("invalid_scope")
        discoveries = cls._source_names(
            payload.get("discovery_sources", []), DISCOVERY_SOURCES, "discovery"
        )
        candidates = cls._source_names(
            payload.get("candidate_sources", []), CANDIDATE_SOURCES, "candidate"
        )
        if enabled and (not discoveries or not candidates):
            raise ProfileError("enabled_profile_requires_sources")
        if any(scope not in DISCOVERY_SOURCES[name]["scopes"] for name in discoveries):
            raise ProfileError("source_scope_mismatch")
        raw_rules = copy.deepcopy(payload.get("rules") or {})
        if not isinstance(raw_rules, dict):
            raise ProfileError("invalid_rules")
        allowed_rules = set(asdict(RuleSet()))
        if set(raw_rules) - allowed_rules:
            raise ProfileError("invalid_rules")
        try:
            converted = copy.deepcopy(raw_rules)
            for key in ("chinese_subtitles", "uncensored", "uhd"):
                if key in converted:
                    converted[key] = RuleMode(converted[key])
            for key in ("include_keywords", "exclude_keywords"):
                if key in converted:
                    values = converted[key]
                    if not isinstance(values, list) or len(values) > 20:
                        raise ValueError
                    if any(not isinstance(value, str) or len(value) > 100 for value in values):
                        raise ValueError
                    converted[key] = tuple(values)
            RuleSet(**converted)
        except (TypeError, ValueError) as exc:
            raise ProfileError("invalid_rules") from exc
        settings = {
            "scope": scope,
            "discovery_sources": discoveries,
            "candidate_sources": candidates,
            "rules": raw_rules,
            "auto_submit": auto_submit,
        }
        return {
            "name": name.strip(),
            "settings": settings,
            "destination_subdir": destination,
            "top_n": top_n,
            "daily_time": daily_time,
            "enabled": enabled,
        }

    @staticmethod
    def _source_names(values, catalog, kind) -> list[str]:
        if not isinstance(values, list) or len(values) > len(catalog):
            raise ProfileError(f"invalid_{kind}_sources")
        if any(not isinstance(value, str) or value not in catalog for value in values):
            raise ProfileError(f"invalid_{kind}_sources")
        return list(dict.fromkeys(values))
