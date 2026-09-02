from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tuntu.db import Database, Repository
from tuntu.db.migration import migrate_database
from tuntu.downloads.poller import DownloadPoller
from tuntu.scheduler import TuntuScheduler


class FakeJob:
    def __init__(self, func, trigger, options):
        self.func = func
        self.trigger = trigger
        self.id = options["id"]
        self.options = options


class FakeSchedulerBackend:
    def __init__(self):
        self.jobs = {}
        self.started = False
        self.shutdown_calls = []

    def add_job(self, func, trigger, **options):
        job = FakeJob(func, trigger, options)
        self.jobs[job.id] = job
        return job

    def remove_job(self, job_id):
        del self.jobs[job_id]

    def get_jobs(self):
        return list(self.jobs.values())

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)


class FakeRunService:
    def __init__(self):
        self.calls = []

    def execute(self, profile_id, *, trigger, force_dry_run=False):
        self.calls.append((profile_id, trigger, force_dry_run))


class FakePoller:
    def __init__(self):
        self.calls = 0

    def poll_once(self):
        self.calls += 1


class FakeDownloadService:
    def __init__(self):
        self.calls = []
        self.fail_task_id = None

    def poll(self, task_id):
        self.calls.append(task_id)
        if task_id == self.fail_task_id:
            raise RuntimeError("private fixture detail")


class FakeWatchlistRunner:
    def __init__(self):
        self.calls = []

    def run(self, watchlist_id, *, force_dry_run=False, trigger="scheduled"):
        self.calls.append((watchlist_id, force_dry_run, trigger))


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "tuntu.db"
        migrate_database(self.db_path, root / "backups")
        self.database = Database(self.db_path)
        self.repo = Repository(self.database)

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()


class SchedulerTests(SchedulerTestCase):
    def scheduler(self, backend=None):
        return TuntuScheduler(
            repository=self.repo,
            run_service=FakeRunService(),
            download_poller=FakePoller(),
            timezone_name="Asia/Shanghai",
            poll_interval_seconds=300,
            max_workers=2,
            backend=backend or FakeSchedulerBackend(),
        )

    def test_only_enabled_active_profiles_are_scheduled_and_removed_on_sync(self):
        enabled_id = self.repo.create_profile(
            "Enabled", {}, destination_subdir="enabled", daily_time="09:30"
        )
        self.repo.create_profile(
            "Disabled",
            {},
            destination_subdir="disabled",
            daily_time="10:30",
            enabled=False,
        )
        archived_id = self.repo.create_profile(
            "Archived", {}, destination_subdir="archived", daily_time="11:30"
        )
        self.repo.archive_profile(archived_id)
        backend = FakeSchedulerBackend()
        scheduler = self.scheduler(backend)

        sync = scheduler.sync()

        self.assertEqual(sync.profile_jobs, 1)
        self.assertEqual(set(backend.jobs), {f"profile:{enabled_id}", "downloads:poll"})
        job = backend.jobs[f"profile:{enabled_id}"]
        self.assertEqual(job.options["max_instances"], 1)
        self.assertTrue(job.options["coalesce"])
        self.assertEqual(job.options["misfire_grace_time"], 1)
        next_fire = job.trigger.get_next_fire_time(
            None, datetime(2026, 8, 13, 2, 0, tzinfo=UTC)
        )
        self.assertGreater(next_fire, datetime(2026, 8, 13, 2, 0, tzinfo=UTC))

        self.repo.set_profile_enabled(enabled_id, False)
        scheduler.sync()
        self.assertEqual(set(backend.jobs), {"downloads:poll"})

    def test_timezone_reconfiguration_replaces_future_trigger_without_running_now(self):
        profile_id = self.repo.create_profile(
            "Fixture", {}, destination_subdir="fixture", daily_time="09:30:15"
        )
        backend = FakeSchedulerBackend()
        run_service = FakeRunService()
        scheduler = TuntuScheduler(
            repository=self.repo,
            run_service=run_service,
            download_poller=FakePoller(),
            timezone_name="Asia/Shanghai",
            poll_interval_seconds=300,
            backend=backend,
        )
        scheduler.sync()

        scheduler.reconfigure_timezone("UTC")

        job = backend.jobs[f"profile:{profile_id}"]
        self.assertEqual(str(job.trigger.timezone), "UTC")
        self.assertEqual(run_service.calls, [])
        self.assertEqual(len([key for key in backend.jobs if key.startswith("profile:")]), 1)

    def test_enabled_watchlist_automation_is_scheduled_and_executes(self):
        profile_id = self.repo.create_profile(
            "Fixture", {}, destination_subdir="fixture"
        )
        watchlist_id = self.repo.create_watchlist(
            "Catalog", "keyword", "fixture", []
        )
        self.repo.update_watchlist_automation(
            watchlist_id,
            {
                "profile_id": profile_id,
                "daily_time": "04:30",
                "enabled": True,
                "auto_submit": False,
            },
        )
        backend = FakeSchedulerBackend()
        watchlists = FakeWatchlistRunner()
        scheduler = TuntuScheduler(
            repository=self.repo,
            run_service=FakeRunService(),
            download_poller=FakePoller(),
            watchlist_runner=watchlists,
            timezone_name="Asia/Shanghai",
            poll_interval_seconds=300,
            backend=backend,
        )

        result = scheduler.sync()

        self.assertEqual(result.watchlist_jobs, 1)
        self.assertIn(f"watchlist:{watchlist_id}", backend.jobs)
        backend.jobs[f"watchlist:{watchlist_id}"].func(watchlist_id)
        self.assertEqual(watchlists.calls, [(watchlist_id, False, "scheduled")])

    def test_start_marks_interrupted_runs_failed_before_accepting_future_jobs(self):
        profile_id = self.repo.create_profile(
            "Fixture", {}, destination_subdir="fixture", daily_time="09:30"
        )
        run_id = self.repo.create_run(profile_id, {}, trigger="scheduled")
        backend = FakeSchedulerBackend()
        scheduler = self.scheduler(backend)

        recovered = scheduler.start()

        self.assertEqual(recovered, 1)
        self.assertEqual(self.repo.get_run(run_id).status, "failed")
        self.assertTrue(backend.started)

    def test_invalid_daily_time_is_not_scheduled_and_is_audited(self):
        profile_id = self.repo.create_profile(
            "Invalid", {}, destination_subdir="invalid", daily_time="25:99"
        )
        backend = FakeSchedulerBackend()
        scheduler = self.scheduler(backend)

        result = scheduler.sync()

        self.assertEqual(result.invalid_profiles, (profile_id,))
        self.assertNotIn(f"profile:{profile_id}", backend.jobs)
        self.assertEqual(self.repo.count_audit_events("profile_schedule_invalid"), 1)


class DownloadPollerTests(SchedulerTestCase):
    def test_new_poller_instance_recovers_all_unfinished_states_and_isolates_errors(self):
        profile_id = self.repo.create_profile("Fixture", {}, destination_subdir="fixture")
        task_ids = []
        statuses = ["submitting", "submitted", "attention_required", "completed"]
        for index, status in enumerate(statuses, 1):
            content_id = self.repo.upsert_content(
                "test", f"ITEM-{index}", f"item-{index}"
            )
            candidate_id = self.repo.upsert_candidate(str(index) * 40)
            task_id = self.repo.claim_download(
                "clouddrive2", profile_id, content_id, candidate_id
            )
            if status != "submitting":
                self.repo.append_download_event(task_id, status, {"kind": "fixture"})
            task_ids.append(task_id)

        service = FakeDownloadService()
        service.fail_task_id = task_ids[1]
        restarted_repo = Repository(Database(self.db_path))
        try:
            result = DownloadPoller(restarted_repo, service).poll_once()
        finally:
            restarted_repo.database.dispose()

        self.assertEqual(result.attempted, 3)
        self.assertEqual(result.succeeded, 2)
        self.assertEqual(result.failed, 1)
        self.assertEqual(set(service.calls), set(task_ids[:3]))
        self.assertEqual(self.repo.count_audit_events("download_poll_failed"), 1)


if __name__ == "__main__":
    unittest.main()
