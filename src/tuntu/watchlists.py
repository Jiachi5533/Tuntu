from __future__ import annotations

import copy
import re
import unicodedata
from collections import Counter
from urllib.parse import urlsplit

from tuntu.providers.attributes import normalize_jav_identity
from tuntu.profiles import CANDIDATE_SOURCES
from tuntu.runs.service import RunExecution


SUBJECT_TYPES = frozenset({"person", "series", "keyword"})
ITEM_STATES = frozenset({"pending", "owned", "ignored"})
_BLOCKED_METADATA_KEYS = ("magnet", "torrent", "download_url", "download_uri")
_DAILY_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
_AUTOMATION_DEFAULTS = {
    "profile_id": None,
    "daily_time": None,
    "enabled": False,
    "auto_submit": False,
}


class WatchlistError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class WatchlistService:
    def __init__(
        self,
        repository,
        manual_service=None,
        *,
        runtime=None,
        scheduler_sync=None,
    ):
        self.repository = repository
        self.manual_service = manual_service
        self.runtime = runtime
        self._scheduler_sync = scheduler_sync or (lambda: None)

    def create(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {
            "name",
            "subject_type",
            "query",
            "aliases",
        }:
            raise WatchlistError("invalid_request")
        name = self._text(payload.get("name"), 200, "invalid_watchlist_name")
        query = self._text(payload.get("query"), 300, "invalid_watchlist_query")
        subject_type = payload.get("subject_type")
        if subject_type not in SUBJECT_TYPES:
            raise WatchlistError("invalid_subject_type")
        aliases = payload.get("aliases", [])
        if not isinstance(aliases, list) or len(aliases) > 20:
            raise WatchlistError("invalid_aliases")
        try:
            normalized_aliases = list(
                dict.fromkeys(self._text(value, 300, "invalid_aliases") for value in aliases)
            )
        except WatchlistError as exc:
            raise WatchlistError("invalid_aliases") from exc
        watchlist_id = self.repository.create_watchlist(
            name, subject_type, query, normalized_aliases
        )
        self.repository.add_audit_event(
            "watchlist_created", entity_type="watchlist", entity_id=str(watchlist_id)
        )
        return self.get(watchlist_id)

    def list(self) -> list[dict]:
        result = []
        for row in self.repository.list_watchlists():
            detail = self.get(row["id"])
            result.append({**row, "summary": detail["summary"]})
        return result

    def get(self, watchlist_id: int) -> dict:
        detail = self.repository.get_watchlist_detail(watchlist_id)
        if detail is None:
            raise WatchlistError("watchlist_not_found")
        items = [self._item(item) for item in detail["items"]]
        counts = Counter(item["state"] for item in items)
        summary = {"total": len(items)}
        summary.update({state: count for state, count in counts.items() if count})
        return {
            **detail,
            "automation": self._automation(detail.get("automation")),
            "items": items,
            "summary": summary,
        }

    def configure_automation(self, watchlist_id: int, payload: dict) -> dict:
        self.get(watchlist_id)
        allowed = {
            "profile_id",
            "daily_time",
            "enabled",
            "auto_submit",
            "rights_confirmed",
        }
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise WatchlistError("invalid_request")
        profile_id = payload.get("profile_id")
        if profile_id is not None and (
            isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1
        ):
            raise WatchlistError("invalid_profile_id")
        daily_time = payload.get("daily_time") or None
        if daily_time is not None and (
            not isinstance(daily_time, str) or not _DAILY_TIME.fullmatch(daily_time)
        ):
            raise WatchlistError("invalid_daily_time")
        enabled = payload.get("enabled", False)
        auto_submit = payload.get("auto_submit", False)
        if not isinstance(enabled, bool) or not isinstance(auto_submit, bool):
            raise WatchlistError("invalid_request")
        if enabled and (profile_id is None or daily_time is None):
            raise WatchlistError("watchlist_automation_incomplete")
        profile = None
        if profile_id is not None:
            profile = self.repository.get_profile(profile_id)
            if profile is None:
                raise WatchlistError("profile_not_found")
            if profile.archived_at is not None:
                raise WatchlistError("profile_archived")
            query_sources = {
                name
                for name, source in CANDIDATE_SOURCES.items()
                if source.get("probe_mode") == "query"
            }
            if not query_sources.intersection(
                profile.settings.get("candidate_sources", [])
            ):
                raise WatchlistError("watchlist_requires_query_source")
        if auto_submit:
            if profile is None:
                raise WatchlistError("watchlist_automation_incomplete")
            if payload.get("rights_confirmed") is not True:
                raise WatchlistError("rights_confirmation_required")

        automation = {
            "profile_id": profile_id,
            "daily_time": daily_time,
            "enabled": enabled,
            "auto_submit": auto_submit,
        }
        try:
            self.repository.update_watchlist_automation(watchlist_id, automation)
        except KeyError as exc:
            raise WatchlistError("watchlist_not_found") from exc
        self.repository.add_audit_event(
            "watchlist_automation_updated",
            entity_type="watchlist",
            entity_id=str(watchlist_id),
            details={
                **automation,
                "rights_confirmed": bool(auto_submit),
            },
        )
        self._scheduler_sync()
        return self.get(watchlist_id)

    def run(
        self,
        watchlist_id: int,
        *,
        force_dry_run: bool = False,
        trigger: str = "manual",
    ) -> RunExecution:
        detail = self.get(watchlist_id)
        automation = detail["automation"]
        if trigger == "scheduled" and not automation["enabled"]:
            return RunExecution(None, "skipped", "watchlist_disabled")
        profile_id = automation["profile_id"]
        if profile_id is None:
            raise WatchlistError("watchlist_automation_incomplete")
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise WatchlistError("profile_not_found")
        if profile.archived_at is not None:
            raise WatchlistError("profile_archived")
        pending = [
            item["raw_key"]
            for item in detail["items"]
            if item["state"] == "pending"
        ][: profile.top_n]
        if not pending:
            self.repository.add_audit_event(
                "watchlist_run_skipped",
                entity_type="watchlist",
                entity_id=str(watchlist_id),
                details={"reason": "no_pending_items"},
            )
            return RunExecution(None, "skipped", "no_pending_items")
        if self.runtime is None:
            raise WatchlistError("runtime_unavailable")
        execution = self.runtime.require().run_service.execute(
            profile_id,
            trigger="watchlist",
            force_dry_run=force_dry_run,
            manual_raw_keys=pending,
            auto_submit_override=automation["auto_submit"],
        )
        self.repository.add_audit_event(
            "watchlist_run_finished",
            entity_type="watchlist",
            entity_id=str(watchlist_id),
            details={
                "run_id": execution.run_id,
                "status": execution.status,
                "force_dry_run": force_dry_run,
                "item_count": len(pending),
            },
        )
        return execution

    def import_items(
        self,
        watchlist_id: int,
        *,
        source_name: str,
        items: list[dict],
    ) -> dict:
        self.get(watchlist_id)
        source_name = self._text(source_name, 100, "invalid_source_name")
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise WatchlistError("invalid_items")
        imported = 0
        for value in items:
            item = self._validate_import_item(value)
            namespace, normalized_key = self._identity(
                item["namespace"], item["key"]
            )
            content_id = self.repository.upsert_content(
                namespace,
                item["key"],
                normalized_key,
                item["title"] or normalized_key,
                metadata=item["metadata"],
            )
            imported += int(
                self.repository.add_watchlist_item(
                    watchlist_id, content_id, source_name=source_name
                )
            )
        self.repository.add_audit_event(
            "watchlist_items_imported",
            entity_type="watchlist",
            entity_id=str(watchlist_id),
            details={"source_name": source_name, "received": len(items), "imported": imported},
        )
        detail = self.get(watchlist_id)
        return {"received": len(items), "imported": imported, **detail}

    def set_item_state(
        self, watchlist_id: int, content_item_id: int, state: str
    ) -> dict:
        if state not in ITEM_STATES:
            raise WatchlistError("invalid_watchlist_item_state")
        if not self.repository.set_watchlist_item_state(
            watchlist_id, content_item_id, state
        ):
            raise WatchlistError("watchlist_item_not_found")
        self.repository.add_audit_event(
            "watchlist_item_state_changed",
            entity_type="content_item",
            entity_id=str(content_item_id),
            details={"watchlist_id": watchlist_id, "state": state},
        )
        item = self.repository.get_watchlist_item(watchlist_id, content_item_id)
        if item is None:
            raise WatchlistError("watchlist_item_not_found")
        return self._item(item)

    def submit_authorized_magnet(
        self,
        watchlist_id: int,
        content_item_id: int,
        *,
        profile_id: int,
        magnet_uri: str,
        rights_confirmed: bool,
        confirmed: bool,
    ) -> dict:
        if not rights_confirmed:
            raise WatchlistError("rights_confirmation_required")
        item = self.repository.get_watchlist_item(watchlist_id, content_item_id)
        if item is None:
            raise WatchlistError("watchlist_item_not_found")
        if self.manual_service is None:
            raise WatchlistError("runtime_unavailable")
        return self.manual_service.submit_authorized_magnet(
            profile_id,
            content_item_id,
            magnet_uri,
            confirmed=confirmed,
        )

    @classmethod
    def _validate_import_item(cls, value: dict) -> dict:
        if not isinstance(value, dict) or set(value) - {
            "namespace",
            "key",
            "title",
            "metadata",
        }:
            raise WatchlistError("invalid_items")
        namespace = cls._text(
            value.get("namespace", "general"), 100, "invalid_items"
        ).casefold()
        key = cls._text(value.get("key"), 300, "invalid_items")
        title = value.get("title", "")
        if not isinstance(title, str) or len(title) > 1000:
            raise WatchlistError("invalid_items")
        metadata = copy.deepcopy(value.get("metadata") or {})
        if not isinstance(metadata, dict):
            raise WatchlistError("invalid_items")
        cls._require_metadata_only(metadata)
        for field in ("source_url", "cover_url"):
            if field not in metadata:
                continue
            url = metadata[field]
            if not isinstance(url, str) or len(url) > 2_000:
                raise WatchlistError("invalid_metadata_url")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise WatchlistError("invalid_metadata_url")
        return {
            "namespace": namespace,
            "key": key,
            "title": title.strip(),
            "metadata": metadata,
        }

    @classmethod
    def _require_metadata_only(cls, value) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = str(key).casefold()
                if any(marker in normalized_key for marker in _BLOCKED_METADATA_KEYS):
                    raise WatchlistError("metadata_only_required")
                cls._require_metadata_only(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                cls._require_metadata_only(nested)
        elif isinstance(value, str) and value.strip().casefold().startswith("magnet:"):
            raise WatchlistError("metadata_only_required")

    @staticmethod
    def _identity(namespace: str, raw_key: str) -> tuple[str, str]:
        if namespace == "jav":
            return normalize_jav_identity(raw_key)
        normalized = " ".join(
            unicodedata.normalize("NFKC", raw_key).strip().split()
        ).casefold()
        return namespace, normalized

    @staticmethod
    def _item(item: dict) -> dict:
        download = item.get("download")
        if download is not None:
            state = "downloaded" if download["status"] == "completed" else download["status"]
        else:
            state = item["state"]
        return {**item, "state": state}

    @staticmethod
    def _automation(value: dict | None) -> dict:
        return {**_AUTOMATION_DEFAULTS, **copy.deepcopy(value or {})}

    @staticmethod
    def _text(value, maximum: int, code: str) -> str:
        if not isinstance(value, str):
            raise WatchlistError(code)
        normalized = " ".join(value.strip().split())
        if not normalized or len(normalized) > maximum:
            raise WatchlistError(code)
        return normalized
