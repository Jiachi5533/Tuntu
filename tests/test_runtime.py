from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tuntu.db import Database, Repository
from tuntu.db.migration import migrate_database
from tuntu.models import ContentItem, RankingEvidence
from tuntu.runtime import RuntimeManager
from tuntu.settings import SettingsService


class RankingProvider:
    name = "javdb_ranking"
    kind = "discovery"

    def collect(self, scope, *, run_id):
        return [
            ContentItem(
                namespace="jav",
                raw_key="ABC-1",
                normalized_key="ABC-001",
                rankings=[RankingEvidence(self.name, 1, "ABC-1", scope)],
                metadata={"javdb_detail_path": "/v/fixture"},
            )
        ]


class DetailProvider:
    name = "javdb_detail"
    kind = "candidate"

    def __init__(self):
        self.paths = []
        self.keys = []

    def search(self, item, *, run_id):
        self.paths.append(item.metadata.get("javdb_detail_path"))
        self.keys.append(item.normalized_key)
        return []


class RuntimeProbeTests(unittest.TestCase):
    def test_javdb_detail_probe_queries_the_requested_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            migrate_database(root / "tuntu.db", root / "backups")
            database = Database(root / "tuntu.db")
            try:
                repository = Repository(database)
                manager = RuntimeManager(
                    repository, SettingsService(repository), start_scheduler=False
                )
                detail = DetailProvider()
                manager.run_service = object()
                manager.candidate_providers = {"javdb_detail": detail}

                result = manager.probe_source(
                    "javdb_detail", query="ABC-1", scope="weekly"
                )

                self.assertEqual(result["status"], "success")
                self.assertEqual(detail.paths, [None])
                self.assertEqual(detail.keys, ["ABC-001"])
                self.assertEqual(
                    repository.get_source_health(
                        "candidate", "javdb_detail"
                    ).last_result_count,
                    0,
                )
            finally:
                database.dispose()


if __name__ == "__main__":
    unittest.main()
