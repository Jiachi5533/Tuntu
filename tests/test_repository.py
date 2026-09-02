from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tuntu.db import (
    Database,
    DatabaseLocked,
    IdempotencyConflict,
    Repository,
    RunAlreadyActive,
)
from tuntu.db.migration import migrate_database
from tuntu.models import CandidateEvidence, RuleReason, TruthValue


class RepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "tuntu.db"
        migrate_database(self.db_path, self.root / "backups")
        self.database = Database(self.db_path, busy_timeout_ms=100)
        self.repo = Repository(self.database)

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()


class SchemaTests(RepositoryTestCase):
    def test_initial_migration_creates_required_tables_and_foreign_keys(self):
        inspector = inspect(self.database.engine)
        required_tables = {
            "users",
            "sessions",
            "setup_tokens",
            "settings",
            "profiles",
            "profile_sources",
            "runs",
            "run_source_results",
            "content_items",
            "run_items",
            "candidates",
            "candidate_evidence",
            "evaluations",
            "download_tasks",
            "download_events",
            "source_health",
            "audit_events",
        }

        self.assertTrue(required_tables <= set(inspector.get_table_names()))
        self.assertEqual(
            {fk["referred_table"] for fk in inspector.get_foreign_keys("run_items")},
            {"runs", "content_items"},
        )
        self.assertEqual(
            {fk["referred_table"] for fk in inspector.get_foreign_keys("download_tasks")},
            {"profiles", "content_items", "candidates", "run_items", "download_tasks"},
        )

    def test_content_identity_and_btih_have_database_unique_constraints(self):
        content_id = self.repo.upsert_content("jav", "ABC-1", "abc-001", "Example")
        same_content_id = self.repo.upsert_content("jav", "ABC001", "abc-001", "Other")
        candidate_id = self.repo.upsert_candidate("a" * 40)
        same_candidate_id = self.repo.upsert_candidate("a" * 40)

        self.assertEqual(content_id, same_content_id)
        self.assertEqual(candidate_id, same_candidate_id)
        with self.database.session() as session:
            with self.assertRaises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO content_items "
                        "(namespace, raw_key, normalized_key, title) "
                        "VALUES ('jav', 'duplicate', 'abc-001', '')"
                    )
                )


class RoundTripTests(RepositoryTestCase):
    def test_profile_enabled_state_controls_schedulable_profiles(self):
        enabled_id = self.repo.create_profile(
            "Enabled",
            {},
            destination_subdir="enabled",
            daily_time="09:30",
        )
        disabled_id = self.repo.create_profile(
            "Disabled",
            {},
            destination_subdir="disabled",
            daily_time="10:30",
            enabled=False,
        )

        self.assertTrue(self.repo.get_profile(enabled_id).enabled)
        self.assertFalse(self.repo.get_profile(disabled_id).enabled)
        self.assertEqual(
            [profile.id for profile in self.repo.list_schedulable_profiles()],
            [enabled_id],
        )

        self.repo.set_profile_enabled(enabled_id, False)
        self.repo.set_profile_enabled(disabled_id, True)
        self.assertEqual(
            [profile.id for profile in self.repo.list_schedulable_profiles()],
            [disabled_id],
        )

    def test_database_allows_only_one_running_run_per_profile(self):
        profile_id = self.repo.create_profile("Weekly", {}, destination_subdir="weekly")
        first_id = self.repo.create_run(profile_id, {"version": 1}, trigger="scheduled")

        with self.assertRaises(RunAlreadyActive):
            self.repo.create_run(profile_id, {"version": 2}, trigger="scheduled")

        self.repo.finish_run(first_id, "success", {"items": 0})
        second_id = self.repo.create_run(profile_id, {"version": 3}, trigger="manual")
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.repo.get_run(first_id).status, "success")
        self.assertEqual(self.repo.get_run(first_id).stats, {"items": 0})

    def test_interrupted_runs_are_failed_on_startup_recovery(self):
        profile_id = self.repo.create_profile("Weekly", {}, destination_subdir="weekly")
        run_id = self.repo.create_run(profile_id, {}, trigger="scheduled")

        recovered = self.repo.fail_interrupted_runs()

        self.assertEqual(recovered, 1)
        run = self.repo.get_run(run_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.stats["error_code"], "process_interrupted")

    def test_run_snapshot_is_independent_and_immutable_after_profile_changes(self):
        profile_id = self.repo.create_profile(
            "Weekly", {"min_seeders": 3, "sources": ["one"]}, destination_subdir="weekly"
        )
        run_id = self.repo.create_run(
            profile_id,
            {"min_seeders": 3, "sources": ["one"]},
            trigger="manual",
        )

        self.repo.update_profile_settings(profile_id, {"min_seeders": 99, "sources": ["two"]})

        self.assertEqual(
            self.repo.get_run_snapshot(run_id),
            {"min_seeders": 3, "sources": ["one"]},
        )
        with self.database.session() as session:
            with self.assertRaises(DBAPIError):
                session.execute(
                    text("UPDATE runs SET config_snapshot = '{}' WHERE id = :id"),
                    {"id": run_id},
                )

    def test_candidate_evidence_and_evaluation_reasons_round_trip(self):
        profile_id = self.repo.create_profile("Weekly", {}, destination_subdir="weekly")
        run_id = self.repo.create_run(profile_id, {}, trigger="manual")
        content_id = self.repo.upsert_content("jav", "ABC-1", "abc-001", "Example")
        run_item_id = self.repo.add_run_item(
            run_id,
            content_id,
            "selected",
            rankings=[{"source": "weekly", "rank": 1, "raw_key": "ABC-1"}],
        )
        candidate_id = self.repo.upsert_candidate("b" * 40)
        evidence = CandidateEvidence(
            source="fixture",
            magnet_uri="magnet:?xt=urn:btih:" + "b" * 40,
            title="Example CHS",
            size_mb=123.5,
            seeders=7,
            chinese_subtitles=TruthValue.YES,
            uncensored=TruthValue.NO,
            uhd=TruthValue.UNKNOWN,
            notes=("title:chs",),
        )
        evidence_id = self.repo.add_candidate_evidence(run_item_id, candidate_id, evidence)
        self.repo.add_candidate_evidence(
            run_item_id,
            candidate_id,
            CandidateEvidence(
                source="conflicting-fixture",
                magnet_uri="magnet:?xt=urn:btih:" + "b" * 40,
                title="Example alternate",
                size_mb=456.0,
                seeders=9,
                chinese_subtitles=TruthValue.NO,
                uncensored=TruthValue.UNKNOWN,
                uhd=TruthValue.UNKNOWN,
            ),
        )
        reasons = [
            RuleReason("uhd_required", "仅接受已确认的UHD候选"),
            RuleReason("size_below_min", "体积低于最小值"),
        ]
        evaluation_id = self.repo.add_evaluation(
            run_item_id, candidate_id, accepted=False, reasons=reasons
        )

        self.assertEqual(self.repo.get_candidate_evidence(evidence_id), evidence)
        self.assertEqual(self.repo.get_evaluation_reasons(evaluation_id), reasons)
        aggregate = self.repo.get_run_detail(run_id)["items"][0]["evaluations"][0][
            "aggregate"
        ]
        self.assertEqual(aggregate["chinese_subtitles"], "unknown")
        self.assertEqual(aggregate["uncensored"], "no")
        self.assertIsNone(aggregate["size_mb"])
        self.assertEqual(aggregate["seeders"], 9)

    def test_latest_ranking_snapshot_preserves_cover_metadata_and_rank_order(self):
        profile_id = self.repo.create_profile(
            "Weekly", {}, destination_subdir="weekly"
        )
        run_id = self.repo.create_run(profile_id, {}, trigger="manual")
        second_id = self.repo.upsert_content(
            "jav",
            "XYZ-2",
            "XYZ-002",
            "Second",
            metadata={
                "cover_url": "https://images.example/xyz.webp",
                "source_url": "https://source.example/xyz",
            },
        )
        first_id = self.repo.upsert_content(
            "jav",
            "ABC-1",
            "ABC-001",
            "First",
            metadata={"cover_url": "https://images.example/abc.webp"},
        )
        self.repo.add_run_item(
            run_id,
            second_id,
            "no_candidate",
            rankings=[{"source": "weekly", "rank": 2, "raw_key": "XYZ-2"}],
        )
        self.repo.add_run_item(
            run_id,
            first_id,
            "selected",
            rankings=[{"source": "weekly", "rank": 1, "raw_key": "ABC-1"}],
        )
        self.repo.finish_run(run_id, "success", {"items_discovered": 2})

        snapshot = self.repo.get_latest_ranking_snapshot()

        self.assertEqual(snapshot["run_id"], run_id)
        self.assertEqual(snapshot["profile_name"], "Weekly")
        self.assertEqual(
            [item["normalized_key"] for item in snapshot["items"]],
            ["abc-001", "xyz-002"],
        )
        self.assertEqual(
            snapshot["items"][0]["metadata"]["cover_url"],
            "https://images.example/abc.webp",
        )
        self.assertEqual(
            self.repo.get_run_detail(run_id)["items"][1]["metadata"]["cover_url"],
            "https://images.example/abc.webp",
        )

    def test_download_events_are_ordered_and_immutable(self):
        profile_id = self.repo.create_profile("Weekly", {}, destination_subdir="weekly")
        content_id = self.repo.upsert_content("jav", "ABC-1", "abc-001", "Example")
        candidate_id = self.repo.upsert_candidate("c" * 40)
        task_id = self.repo.claim_download(
            "cd2-main", profile_id, content_id, candidate_id, initial_status="submitting"
        )
        first_time = datetime.now(UTC)
        self.repo.append_download_event(
            task_id,
            "submitted",
            {"kind": "accepted"},
            occurred_at=first_time,
        )
        self.repo.append_download_event(
            task_id,
            "completed",
            {"kind": "stable_files"},
            occurred_at=first_time + timedelta(seconds=1),
        )

        events = self.repo.list_download_events(task_id)
        self.assertEqual([event.status for event in events], ["submitting", "submitted", "completed"])
        self.assertEqual([event.sequence for event in events], [1, 2, 3])
        with self.database.session() as session:
            with self.assertRaises(DBAPIError):
                session.execute(
                    text("UPDATE download_events SET status = 'failed' WHERE download_task_id = :id"),
                    {"id": task_id},
                )

    def test_archiving_profile_preserves_run_history(self):
        profile_id = self.repo.create_profile("Weekly", {}, destination_subdir="weekly")
        run_id = self.repo.create_run(profile_id, {"profile_name": "Weekly"}, trigger="manual")

        self.repo.archive_profile(profile_id)

        self.assertTrue(self.repo.is_profile_archived(profile_id))
        self.assertEqual(self.repo.get_run_snapshot(run_id), {"profile_name": "Weekly"})
        with self.database.session() as session:
            with self.assertRaises(IntegrityError):
                session.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": profile_id})


class IdempotencyTests(RepositoryTestCase):
    def _profile_content_candidates(self):
        profile_id = self.repo.create_profile("Weekly", {}, destination_subdir="weekly")
        first_content = self.repo.upsert_content("jav", "A-1", "a-001", "A")
        second_content = self.repo.upsert_content("jav", "B-1", "b-001", "B")
        first_candidate = self.repo.upsert_candidate("d" * 40)
        second_candidate = self.repo.upsert_candidate("e" * 40)
        return profile_id, first_content, second_content, first_candidate, second_candidate

    def test_concurrent_claims_for_same_content_allow_only_one(self):
        profile_id, content_id, _, first_candidate, second_candidate = (
            self._profile_content_candidates()
        )
        barrier = threading.Barrier(2)
        outcomes = []

        def claim(candidate_id):
            repo = Repository(Database(self.db_path, busy_timeout_ms=500))
            try:
                barrier.wait()
                repo.claim_download("cd2-main", profile_id, content_id, candidate_id)
                outcomes.append("claimed")
            except IdempotencyConflict:
                outcomes.append("conflict")
            finally:
                repo.database.dispose()

        threads = [
            threading.Thread(target=claim, args=(first_candidate,)),
            threading.Thread(target=claim, args=(second_candidate,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(outcomes), ["claimed", "conflict"])
        self.assertEqual(self.repo.count_download_tasks(), 1)

    def test_same_btih_for_different_content_allows_only_one(self):
        profile_id, first_content, second_content, candidate_id, _ = (
            self._profile_content_candidates()
        )

        self.repo.claim_download("cd2-main", profile_id, first_content, candidate_id)

        with self.assertRaises(IdempotencyConflict):
            self.repo.claim_download("cd2-main", profile_id, second_content, candidate_id)

    def test_failed_initial_event_rolls_back_task_and_claims(self):
        profile_id, content_id, _, candidate_id, _ = self._profile_content_candidates()

        with self.assertRaises(IntegrityError):
            self.repo.claim_download(
                "cd2-main", profile_id, content_id, candidate_id, initial_status=None
            )

        self.assertEqual(self.repo.count_download_tasks(), 0)
        self.repo.claim_download("cd2-main", profile_id, content_id, candidate_id)
        self.assertEqual(self.repo.count_download_tasks(), 1)

    def test_task_and_claim_survive_database_restart(self):
        profile_id, content_id, _, candidate_id, _ = self._profile_content_candidates()
        task_id = self.repo.claim_download("cd2-main", profile_id, content_id, candidate_id)
        self.database.dispose()

        restarted = Database(self.db_path, busy_timeout_ms=100)
        try:
            task = Repository(restarted).get_download_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, "submitting")
        finally:
            restarted.dispose()

    def test_sqlite_lock_is_reported_as_controlled_error(self):
        profile_id, content_id, _, candidate_id, _ = self._profile_content_candidates()
        locker = sqlite3.connect(self.db_path, timeout=0.1)
        try:
            locker.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(DatabaseLocked):
                self.repo.claim_download("cd2-main", profile_id, content_id, candidate_id)
        finally:
            locker.rollback()
            locker.close()

    def test_concurrent_identity_and_source_health_upserts_are_atomic(self):
        barrier = threading.Barrier(4)
        content_ids = []
        candidate_ids = []

        def upsert():
            repo = Repository(Database(self.db_path, busy_timeout_ms=1_000))
            try:
                barrier.wait()
                content_ids.append(
                    repo.upsert_content("test", "ITEM-1", "item-1")
                )
                candidate_ids.append(repo.upsert_candidate("f" * 40))
                repo.record_source_failure(
                    "candidate", "shared", "network_error", "network_error", 1
                )
            finally:
                repo.database.dispose()

        threads = [threading.Thread(target=upsert) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertEqual(len(set(content_ids)), 1)
        self.assertEqual(len(set(candidate_ids)), 1)
        self.assertEqual(
            self.repo.get_source_health("candidate", "shared").consecutive_failures,
            4,
        )


class SourceHealthTests(RepositoryTestCase):
    def test_failures_increment_without_disabling_and_success_resets_count(self):
        checked_at = datetime.now(UTC)
        self.repo.record_source_failure(
            "candidate", "fixture", "network_timeout", "network_timeout", 100, checked_at
        )
        self.repo.record_source_failure(
            "candidate", "fixture", "invalid_xml", "invalid_xml", 50, checked_at
        )

        failed = self.repo.get_source_health("candidate", "fixture")
        self.assertEqual(failed.consecutive_failures, 2)
        self.assertEqual(failed.last_error_code, "invalid_xml")
        self.assertFalse(hasattr(failed, "disabled"))

        self.repo.record_source_success(
            "candidate", "fixture", latency_ms=25, result_count=0, checked_at=checked_at
        )

        recovered = self.repo.get_source_health("candidate", "fixture")
        self.assertEqual(recovered.consecutive_failures, 0)
        self.assertEqual(recovered.last_result_count, 0)
        self.assertIsNone(recovered.last_error_code)


if __name__ == "__main__":
    unittest.main()
