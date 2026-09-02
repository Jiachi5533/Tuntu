from __future__ import annotations

from tuntu.db import IdempotencyConflict
from tuntu.downloaders.clouddrive import resolve_destination, resolve_task_destination
from tuntu.magnet import InvalidMagnet, parse_magnet


class ManualError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ManualService:
    def __init__(self, repository, runtime):
        self.repository = repository
        self.runtime = runtime

    def number_preview(self, profile_id: int, number: str) -> dict:
        if not isinstance(number, str) or not (1 <= len(number.strip()) <= 100):
            raise ManualError("invalid_number")
        profile = self._profile(profile_id)
        runtime = self.runtime.require()
        execution = runtime.run_service.execute(
            profile.id,
            trigger="manual_number",
            force_dry_run=True,
            manual_raw_keys=[number.strip()],
        )
        if execution.run_id is None:
            raise ManualError(execution.skipped_reason or "manual_run_skipped")
        detail = self.repository.get_run_detail(execution.run_id)
        if detail is None or not detail["items"]:
            raise ManualError("manual_run_failed")
        item = detail["items"][0]
        destination = detail["config_snapshot"]["destination"]
        candidates = []
        for value in item["evaluations"]:
            candidate = self._stored_evaluation_dict(value)
            candidate["destination"] = resolve_task_destination(
                destination, candidate["btih"]
            )
            candidate["duplicate"] = self.repository.find_download_by_btih(
                candidate["btih"]
            )
            candidates.append(candidate)
        return {
            "run_id": execution.run_id,
            "profile_id": profile.id,
            "number": number.strip(),
            "namespace": item["namespace"],
            "normalized_key": item["normalized_key"],
            "destination": destination,
            "source_failures": [
                source["error_code"]
                for source in detail["sources"]
                if source["status"] == "failed"
            ],
            "candidates": candidates,
        }

    def magnet_preview(self, profile_id: int, magnet_uri: str, title: str = "") -> dict:
        profile = self._profile(profile_id)
        parsed = self._parse(magnet_uri)
        runtime = self.runtime.require()
        duplicate = self.repository.find_download_by_btih(parsed.btih)
        profile_destination = resolve_destination(
            runtime.client.config.root_path, profile.destination_subdir
        )
        return {
            "profile_id": profile.id,
            "title": self._title(title),
            "btih": parsed.btih,
            "canonical_magnet": parsed.canonical_uri,
            "destination": resolve_task_destination(profile_destination, parsed.btih),
            "duplicate": duplicate,
            "requires_force_confirmation": duplicate is not None,
        }

    def submit_magnet(
        self,
        profile_id: int,
        magnet_uri: str,
        *,
        title: str = "",
        force: bool = False,
        confirmed: bool = False,
    ) -> dict:
        if not confirmed:
            raise ManualError("confirmation_required")
        profile = self._profile(profile_id)
        runtime = self.runtime.require()
        parsed = self._parse(magnet_uri)
        duplicate = self.repository.find_download_by_btih(parsed.btih)
        if duplicate is not None and not force:
            raise ManualError("force_confirmation_required")
        if duplicate is None and force:
            raise ManualError("force_not_required")
        content_id = self.repository.upsert_content(
            "magnet", parsed.btih, parsed.btih, self._title(title) or parsed.btih
        )
        candidate_id = self.repository.upsert_candidate(parsed.btih)
        generation = 0
        supersedes = None
        if force:
            generation = self.repository.next_download_generation(
                "clouddrive2", content_id, candidate_id
            )
            supersedes = duplicate["task_id"] if duplicate else None
        destination = resolve_task_destination(
            resolve_destination(runtime.client.config.root_path, profile.destination_subdir),
            parsed.btih,
        )
        try:
            task_id = runtime.download_service.submit_candidate(
                profile_id=profile.id,
                content_item_id=content_id,
                candidate_id=candidate_id,
                magnet_uri=parsed.canonical_uri,
                destination=destination,
                generation=generation,
                supersedes_task_id=supersedes,
            )
        except IdempotencyConflict as exc:
            raise ManualError("duplicate_download") from exc
        self.repository.add_audit_event(
            "forced_magnet_submit" if force else "manual_magnet_submit",
            entity_type="download_task",
            entity_id=task_id,
            details={"profile_id": profile.id, "supersedes_task_id": supersedes},
        )
        return self.repository.get_download_detail(task_id)

    def submit_authorized_magnet(
        self,
        profile_id: int,
        content_item_id: int,
        magnet_uri: str,
        *,
        confirmed: bool,
    ) -> dict:
        if not confirmed:
            raise ManualError("confirmation_required")
        profile = self._profile(profile_id)
        runtime = self.runtime.require()
        parsed = self._parse(magnet_uri)
        if self.repository.find_download_by_btih(parsed.btih) is not None:
            raise ManualError("duplicate_download")
        candidate_id = self.repository.upsert_candidate(parsed.btih)
        destination = resolve_task_destination(
            resolve_destination(runtime.client.config.root_path, profile.destination_subdir),
            parsed.btih,
        )
        try:
            task_id = runtime.download_service.submit_candidate(
                profile_id=profile.id,
                content_item_id=content_item_id,
                candidate_id=candidate_id,
                magnet_uri=parsed.canonical_uri,
                destination=destination,
            )
        except IdempotencyConflict as exc:
            raise ManualError("duplicate_download") from exc
        self.repository.add_audit_event(
            "authorized_watchlist_magnet_submit",
            entity_type="download_task",
            entity_id=task_id,
            details={"profile_id": profile.id, "content_item_id": content_item_id},
        )
        return self.repository.get_download_detail(task_id)

    def submit_number_candidate(
        self,
        profile_id: int,
        *,
        run_id: str,
        candidate_id: int,
        confirmed: bool,
    ) -> dict:
        if not confirmed:
            raise ManualError("confirmation_required")
        profile = self._profile(profile_id)
        runtime = self.runtime.require()
        selected = self.repository.get_accepted_run_candidate(run_id, candidate_id)
        if selected is None or selected["profile_id"] != profile.id:
            raise ManualError("candidate_not_accepted")
        destination = resolve_task_destination(
            resolve_destination(runtime.client.config.root_path, profile.destination_subdir),
            selected["btih"],
        )
        try:
            task_id = runtime.download_service.submit_candidate(
                profile_id=profile.id,
                content_item_id=selected["content_item_id"],
                candidate_id=candidate_id,
                magnet_uri=selected["magnet_uri"],
                destination=destination,
                run_item_id=selected["run_item_id"],
            )
        except IdempotencyConflict as exc:
            raise ManualError("duplicate_download") from exc
        self.repository.add_audit_event(
            "manual_number_submit",
            entity_type="download_task",
            entity_id=task_id,
            details={"profile_id": profile.id, "run_id": run_id},
        )
        return self.repository.get_download_detail(task_id)

    def _profile(self, profile_id):
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise ManualError("profile_not_found")
        if profile.archived_at is not None:
            raise ManualError("profile_archived")
        return profile

    @staticmethod
    def _parse(value):
        if not isinstance(value, str) or len(value) > 8_000:
            raise ManualError("invalid_magnet")
        try:
            return parse_magnet(value)
        except InvalidMagnet as exc:
            raise ManualError("invalid_magnet") from exc

    @staticmethod
    def _title(value):
        if not isinstance(value, str) or len(value) > 500:
            raise ManualError("invalid_title")
        return value.strip()

    @staticmethod
    def _stored_evaluation_dict(evaluation):
        aggregate = evaluation["aggregate"]
        return {
            "candidate_id": evaluation["candidate_id"],
            "btih": evaluation["btih"],
            **aggregate,
            "accepted": evaluation["accepted"],
            "reasons": evaluation["reasons"],
        }
