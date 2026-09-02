from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from tuntu.downloaders.clouddrive import DirectorySnapshot, FileFact
from tuntu.downloads.completion import CompletionState, observe_completion
from tuntu.downloads.state import DownloadStatus, InvalidStatusTransition, transition_status


def snapshot(*files):
    return DirectorySnapshot(files=tuple(FileFact(path, size) for path, size in files))


class DownloadStateTests(unittest.TestCase):
    def test_happy_path_and_direct_submitted_to_completed_are_valid(self):
        self.assertEqual(
            transition_status(DownloadStatus.SUBMITTING, DownloadStatus.SUBMITTED),
            DownloadStatus.SUBMITTED,
        )
        self.assertEqual(
            transition_status(DownloadStatus.SUBMITTED, DownloadStatus.DOWNLOADING),
            DownloadStatus.DOWNLOADING,
        )
        self.assertEqual(
            transition_status(DownloadStatus.DOWNLOADING, DownloadStatus.COMPLETED),
            DownloadStatus.COMPLETED,
        )
        self.assertEqual(
            transition_status(DownloadStatus.SUBMITTED, DownloadStatus.COMPLETED),
            DownloadStatus.COMPLETED,
        )

    def test_illegal_terminal_transition_is_rejected(self):
        with self.assertRaises(InvalidStatusTransition):
            transition_status(DownloadStatus.COMPLETED, DownloadStatus.DOWNLOADING)

    def test_poll_error_does_not_create_a_status_transition(self):
        self.assertEqual(
            transition_status(
                DownloadStatus.DOWNLOADING,
                None,
                evidence_reliable=False,
            ),
            DownloadStatus.DOWNLOADING,
        )

    def test_attention_threshold_is_explicit_transition(self):
        started = datetime(2026, 8, 13, tzinfo=UTC)
        self.assertEqual(
            transition_status(
                DownloadStatus.SUBMITTED,
                None,
                now=started + timedelta(hours=25),
                attention_after=started + timedelta(hours=24),
            ),
            DownloadStatus.ATTENTION_REQUIRED,
        )
        self.assertEqual(
            transition_status(
                DownloadStatus.DOWNLOADING,
                DownloadStatus.COMPLETED,
                now=started + timedelta(hours=25),
                attention_after=started + timedelta(hours=24),
            ),
            DownloadStatus.COMPLETED,
        )


class CompletionTests(unittest.TestCase):
    def test_requires_new_or_changed_files_and_two_stable_observations(self):
        baseline = snapshot(("/weekly/old.bin", 10))
        state = CompletionState(
            baseline=baseline,
            destination_path="/weekly",
            required_stable_observations=2,
        )

        no_change = observe_completion(state, baseline)
        growing = observe_completion(no_change.state, snapshot(("/weekly/old.bin", 10), ("/weekly/new.bin", 5)))
        changed = observe_completion(growing.state, snapshot(("/weekly/old.bin", 10), ("/weekly/new.bin", 9)))
        stable = observe_completion(changed.state, snapshot(("/weekly/old.bin", 10), ("/weekly/new.bin", 9)))

        self.assertFalse(no_change.completed)
        self.assertFalse(growing.completed)
        self.assertFalse(changed.completed)
        self.assertTrue(stable.completed)
        self.assertEqual(stable.changed_file_count, 1)
        self.assertEqual(stable.changed_total_size, 9)

    def test_changed_existing_file_can_be_owned_after_submission(self):
        state = CompletionState(
            baseline=snapshot(("/weekly/result.bin", 1)),
            destination_path="/weekly",
            required_stable_observations=2,
        )
        first = observe_completion(state, snapshot(("/weekly/result.bin", 10)))
        second = observe_completion(first.state, snapshot(("/weekly/result.bin", 10)))

        self.assertTrue(second.completed)

    def test_empty_or_zero_byte_delta_never_completes(self):
        state = CompletionState(
            baseline=snapshot(),
            destination_path="/weekly",
            required_stable_observations=2,
        )
        first = observe_completion(state, snapshot(("/weekly/empty.bin", 0)))
        second = observe_completion(first.state, snapshot(("/weekly/empty.bin", 0)))

        self.assertFalse(second.completed)

    def test_owned_paths_ignore_later_unrelated_concurrent_files(self):
        state = CompletionState(
            baseline=snapshot(),
            destination_path="/weekly",
            required_stable_observations=2,
        )
        first = observe_completion(state, snapshot(("/weekly/task-a.bin", 10)))
        second = observe_completion(
            first.state,
            snapshot(("/weekly/task-a.bin", 10), ("/weekly/unrelated.bin", 999)),
        )

        self.assertTrue(second.completed)
        self.assertEqual(second.changed_file_count, 1)

    def test_later_files_under_owned_top_level_directory_reset_stability(self):
        state = CompletionState(
            baseline=snapshot(),
            destination_path="/weekly",
            required_stable_observations=2,
        )
        first = observe_completion(
            state, snapshot(("/weekly/task/a.bin", 10))
        )
        second = observe_completion(
            first.state,
            snapshot(("/weekly/task/a.bin", 10), ("/weekly/task/b.bin", 5)),
        )
        third = observe_completion(
            second.state,
            snapshot(("/weekly/task/a.bin", 10), ("/weekly/task/b.bin", 5)),
        )

        self.assertFalse(second.completed)
        self.assertTrue(third.completed)


if __name__ == "__main__":
    unittest.main()
