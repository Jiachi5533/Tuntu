from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from tuntu.db import Database, DestinationBusy, IdempotencyConflict, Repository
from tuntu.db.migration import migrate_database
from tuntu.downloaders.clouddrive import (
    CloudDriveNetworkError,
    CloudDriveRejected,
    DirectorySnapshot,
    ExternalTaskAlreadyExists,
    FileFact,
    ResultUnknown,
    SubmitResult,
    TaskSignal,
)
from tuntu.downloads.service import ConfirmationRequired, DownloadService
from tuntu.downloads.state import DownloadStatus


def snapshot(*files):
    return DirectorySnapshot(tuple(FileFact(path, size) for path, size in files))


class FakeCloudDriveClient:
    def __init__(self):
        self.config = SimpleNamespace(
            attention_after_seconds=86_400,
            required_stable_observations=2,
        )
        self.submit_error = None
        self.baseline = snapshot()
        self.signals = []
        self.snapshots = []
        self.submissions = []
        self.destinations = []

    def ensure_destination(self, destination):
        self.destinations.append(destination)

    def submit(self, magnet_uri, destination):
        btih = magnet_uri.split("urn:btih:", 1)[1].split("&", 1)[0].casefold()
        self.submissions.append((btih, destination))
        if self.submit_error:
            error = self.submit_error
            if isinstance(error, ResultUnknown):
                error.baseline = self.baseline
                error.btih = btih
                error.destination = destination
            raise error
        return SubmitResult(btih, destination, self.baseline)

    def get_task_signal(self, btih, destination):
        value = self.signals.pop(0) if self.signals else TaskSignal.UNKNOWN
        if isinstance(value, Exception):
            raise value
        return value

    def snapshot(self, destination, *, force_refresh):
        value = self.snapshots.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class DownloadServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "tuntu.db"
        migrate_database(self.db_path, root / "backups")
        self.database = Database(self.db_path)
        self.repo = Repository(self.database)
        self.profile_id = self.repo.create_profile("Fixture", {}, destination_subdir="weekly")
        self.content_id = self.repo.upsert_content("test", "ITEM-1", "item-1", "Fixture")
        self.candidate_id = self.repo.upsert_candidate("a" * 40)
        self.client = FakeCloudDriveClient()
        self.clock = MutableClock(datetime(2026, 8, 13, tzinfo=UTC))
        self.service = DownloadService(self.repo, self.client, now=self.clock)

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()

    def submit(self):
        return self.service.submit_candidate(
            profile_id=self.profile_id,
            content_item_id=self.content_id,
            candidate_id=self.candidate_id,
            magnet_uri="magnet:?xt=urn:btih:" + "a" * 40,
            destination="/weekly",
        )

    def test_accepted_submission_persists_baseline_deadline_and_events(self):
        self.client.baseline = snapshot(("/weekly/old.bin", 10))

        task_id = self.submit()
        task = self.repo.get_download_task(task_id)

        self.assertEqual(task.status, DownloadStatus.SUBMITTED)
        self.assertEqual(task.destination_path, "/weekly")
        self.assertEqual(task.baseline.file_count, 1)
        self.assertEqual(task.attention_after_at, self.clock.value + timedelta(days=1))
        self.assertEqual(
            [event.status for event in self.repo.list_download_events(task_id)],
            ["submitting", "submitted"],
        )

    def test_unknown_task_api_falls_back_to_persisted_stable_file_observations(self):
        task_id = self.submit()
        self.client.signals = [TaskSignal.UNKNOWN, TaskSignal.UNKNOWN]
        self.client.snapshots = [
            snapshot(("/weekly/new.bin", 10)),
            snapshot(("/weekly/new.bin", 10)),
        ]

        self.service.poll(task_id)
        restarted_service = DownloadService(
            Repository(Database(self.db_path)), self.client, now=self.clock
        )
        try:
            restarted_service.poll(task_id)
        finally:
            restarted_service.repository.database.dispose()

        task = self.repo.get_download_task(task_id)
        self.assertEqual(task.status, DownloadStatus.COMPLETED)
        self.assertEqual(
            [event.status for event in self.repo.list_download_events(task_id)],
            ["submitting", "submitted", "downloading", "completed"],
        )

    def test_repeated_no_progress_observations_do_not_duplicate_events(self):
        task_id = self.submit()
        self.client.signals = [TaskSignal.UNKNOWN, TaskSignal.UNKNOWN]
        self.client.snapshots = [snapshot(), snapshot()]

        self.service.poll(task_id)
        self.clock.value += timedelta(minutes=5)
        self.service.poll(task_id)

        events = self.repo.list_download_events(task_id)
        self.assertEqual(
            [event.evidence["kind"] for event in events],
            ["claim_created", "accepted", "no_reliable_progress"],
        )

    def test_completion_uses_configured_stable_observation_count(self):
        self.client.config.required_stable_observations = 3
        task_id = self.submit()
        self.client.signals = [TaskSignal.UNKNOWN] * 3
        self.client.snapshots = [snapshot(("/weekly/new.bin", 10))] * 3

        self.assertEqual(self.service.poll(task_id), DownloadStatus.DOWNLOADING)
        self.assertEqual(self.service.poll(task_id), DownloadStatus.DOWNLOADING)
        self.assertEqual(self.service.poll(task_id), DownloadStatus.COMPLETED)

    def test_reliable_task_signals_map_to_downloading_completed_and_failed(self):
        task_id = self.submit()
        self.client.signals = [TaskSignal.DOWNLOADING, TaskSignal.FINISHED]

        self.service.poll(task_id)
        self.service.poll(task_id)

        self.assertEqual(self.repo.get_download_task(task_id).status, DownloadStatus.COMPLETED)

        second_content = self.repo.upsert_content("test", "ITEM-2", "item-2", "Fixture")
        second_candidate = self.repo.upsert_candidate("b" * 40)
        second_id = self.service.submit_candidate(
            profile_id=self.profile_id,
            content_item_id=second_content,
            candidate_id=second_candidate,
            magnet_uri="magnet:?xt=urn:btih:" + "b" * 40,
            destination="/weekly",
        )
        self.client.signals = [TaskSignal.ERROR]
        self.service.poll(second_id)
        self.assertEqual(self.repo.get_download_task(second_id).status, DownloadStatus.FAILED)

    def test_poll_error_keeps_reliable_status_then_attention_threshold_applies(self):
        task_id = self.submit()
        self.client.signals = [TaskSignal.DOWNLOADING, CloudDriveNetworkError("network_error")]
        self.service.poll(task_id)
        self.service.poll(task_id)
        self.assertEqual(self.repo.get_download_task(task_id).status, DownloadStatus.DOWNLOADING)

        self.clock.value += timedelta(days=2)
        self.client.signals = [CloudDriveNetworkError("network_error")]
        self.service.poll(task_id)
        self.assertEqual(
            self.repo.get_download_task(task_id).status,
            DownloadStatus.ATTENTION_REQUIRED,
        )

    def test_submission_errors_have_distinct_persisted_semantics(self):
        cases = (
            (CloudDriveRejected("explicit_rejection"), DownloadStatus.FAILED),
            (ExternalTaskAlreadyExists("external_task_already_exists"), DownloadStatus.ATTENTION_REQUIRED),
            (ResultUnknown("submission_result_unknown"), DownloadStatus.SUBMITTING),
        )
        for index, (error, expected) in enumerate(cases, 1):
            content = self.repo.upsert_content("test", f"ITEM-{index + 10}", f"item-{index + 10}")
            candidate = self.repo.upsert_candidate(str(index) * 40)
            self.client.submit_error = error
            task_id = self.service.submit_candidate(
                profile_id=self.profile_id,
                content_item_id=content,
                candidate_id=candidate,
                magnet_uri="magnet:?xt=urn:btih:" + str(index) * 40,
                destination=f"/weekly-{index}",
            )
            self.assertEqual(self.repo.get_download_task(task_id).status, expected)
        self.client.submit_error = None

    def test_result_unknown_keeps_global_lock_and_never_switches_candidate(self):
        self.client.submit_error = ResultUnknown("submission_result_unknown")
        task_id = self.submit()

        with self.assertRaises(IdempotencyConflict):
            self.submit()
        self.assertEqual(self.repo.get_download_task(task_id).status, DownloadStatus.SUBMITTING)
        self.assertEqual(len(self.client.submissions), 1)

    def test_destination_blocks_other_submissions_until_owned_paths_are_observed(self):
        first_id = self.submit()
        second_content = self.repo.upsert_content("test", "ITEM-99", "item-99")
        second_candidate = self.repo.upsert_candidate("9" * 40)

        with self.assertRaises(DestinationBusy):
            self.service.submit_candidate(
                profile_id=self.profile_id,
                content_item_id=second_content,
                candidate_id=second_candidate,
                magnet_uri="magnet:?xt=urn:btih:" + "9" * 40,
                destination="/weekly",
            )

        self.client.signals = [TaskSignal.UNKNOWN]
        self.client.snapshots = [snapshot(("/weekly/first.bin", 10))]
        self.service.poll(first_id)
        second_id = self.service.submit_candidate(
            profile_id=self.profile_id,
            content_item_id=second_content,
            candidate_id=second_candidate,
            magnet_uri="magnet:?xt=urn:btih:" + "9" * 40,
            destination="/weekly",
        )
        self.assertEqual(self.repo.get_download_task(second_id).status, DownloadStatus.SUBMITTED)

    def test_candidate_and_magnet_btih_must_match_before_claim(self):
        with self.assertRaises(ValueError):
            self.service.submit_candidate(
                profile_id=self.profile_id,
                content_item_id=self.content_id,
                candidate_id=self.candidate_id,
                magnet_uri="magnet:?xt=urn:btih:" + "b" * 40,
                destination="/weekly",
            )
        self.assertEqual(self.repo.count_download_tasks(), 0)

    def test_manual_completion_requires_confirmation_and_writes_manual_audit(self):
        task_id = self.submit()

        with self.assertRaises(ConfirmationRequired):
            self.service.manual_complete(task_id, confirmed=False)
        self.service.manual_complete(task_id, confirmed=True)

        completed = self.repo.get_download_task(task_id)
        self.assertEqual(completed.status, DownloadStatus.COMPLETED)
        self.assertTrue(completed.manual_completed)
        self.assertEqual(self.repo.list_download_events(task_id)[-1].source, "manual")
        self.assertEqual(self.repo.count_audit_events("manual_download_completion"), 1)

    def test_failed_retry_needs_no_confirmation_but_force_retry_does_and_links_history(self):
        self.client.submit_error = CloudDriveRejected("explicit_rejection")
        failed_id = self.submit()
        self.client.submit_error = None

        retry_id = self.service.retry(failed_id, confirmed=False)
        retry = self.repo.get_download_task(retry_id)
        self.assertEqual(retry.supersedes_task_id, failed_id)
        self.assertEqual(retry.generation, 1)

        self.client.signals = [TaskSignal.DOWNLOADING]
        self.service.poll(retry_id)

        with self.assertRaises(ConfirmationRequired):
            self.service.retry(retry_id, confirmed=False)
        forced_id = self.service.retry(retry_id, confirmed=True)
        forced = self.repo.get_download_task(forced_id)
        self.assertEqual(forced.supersedes_task_id, retry_id)
        self.assertEqual(forced.generation, 2)
        self.assertEqual(self.repo.count_audit_events("forced_download_retry"), 1)


if __name__ == "__main__":
    unittest.main()
