from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from tuntu.db.migration import (
    MigrationFailed,
    UnsupportedSchemaVersion,
    create_backup,
    current_revision,
    latest_revision,
    migrate_database,
)
from tuntu.db.models import Base
from tuntu.db import migration as migration_module


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "tuntu.db"
        self.backup_dir = self.root / "backups"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_database_migrates_to_latest_revision(self):
        migrate_database(self.db_path, self.backup_dir)

        self.assertEqual(current_revision(self.db_path), latest_revision())
        self.assertEqual(list(self.backup_dir.glob("*.db")), [])

    def test_latest_database_startup_repairs_data_permissions(self):
        migrate_database(self.db_path, self.backup_dir)
        os.chmod(self.root, 0o755)
        os.chmod(self.db_path, 0o644)

        migrate_database(self.db_path, self.backup_dir)

        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.db_path).st_mode & 0o777, 0o600)

    def test_latest_migration_matches_orm_metadata(self):
        migrate_database(self.db_path, self.backup_dir)
        engine = create_engine(f"sqlite+pysqlite:///{self.db_path}")
        try:
            with engine.connect() as connection:
                differences = compare_metadata(
                    MigrationContext.configure(connection), Base.metadata
                )
        finally:
            engine.dispose()

        self.assertEqual(differences, [])

    def test_watchlist_automation_column_has_a_safe_disabled_default(self):
        migrate_database(self.db_path, self.backup_dir)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO watchlists (name, subject_type, query, aliases_json) "
                "VALUES ('Fixture', 'keyword', 'fixture', '[]')"
            )
            value = connection.execute(
                "SELECT automation_json FROM watchlists"
            ).fetchone()[0]

        self.assertEqual(value, "{}")

    def test_run_scheduling_upgrade_closes_interrupted_runs_before_unique_index(self):
        command.upgrade(
            migration_module._config(self.db_path), "0002_download_tracking"
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO profiles "
                "(name, settings_json, destination_subdir, top_n) "
                "VALUES ('Fixture', '{}', 'fixture', 20)"
            )
            profile_id = connection.execute(
                "SELECT id FROM profiles"
            ).fetchone()[0]
            for run_id in ("run-one", "run-two"):
                connection.execute(
                    "INSERT INTO runs "
                    "(id, profile_id, status, trigger, config_snapshot, stats_json) "
                    "VALUES (?, ?, 'running', 'scheduled', '{}', '{}')",
                    (run_id, profile_id),
                )

        migrate_database(self.db_path, self.backup_dir)

        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT status, stats_json, finished_at FROM runs ORDER BY id"
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["failed", "failed"])
        self.assertTrue(
            all("process_interrupted_during_upgrade" in row[1] for row in rows)
        )
        self.assertTrue(all(row[2] is not None for row in rows))

    def test_existing_legacy_database_is_backed_up_before_upgrade(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_marker VALUES ('preserved')")

        migrate_database(self.db_path, self.backup_dir)

        backups = list(self.backup_dir.glob("*.db"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as connection:
            self.assertEqual(connection.execute("SELECT value FROM legacy_marker").fetchone()[0], "preserved")

    def test_failed_upgrade_does_not_partially_mutate_original_database(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_marker VALUES ('original')")

        def fail_after_write(work_path):
            with sqlite3.connect(work_path) as connection:
                connection.execute("CREATE TABLE partial_migration (id INTEGER)")
            raise RuntimeError("injected migration failure")

        with self.assertRaises(MigrationFailed):
            migrate_database(
                self.db_path,
                self.backup_dir,
                upgrade_runner=fail_after_write,
            )

        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("legacy_marker", tables)
            self.assertNotIn("partial_migration", tables)
            self.assertEqual(connection.execute("SELECT value FROM legacy_marker").fetchone()[0], "original")

    def test_backup_rotation_keeps_three_most_recent_files(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE marker (value TEXT)")
        start = datetime(2026, 8, 13, tzinfo=UTC)

        for offset in range(5):
            create_backup(
                self.db_path,
                self.backup_dir,
                now=start + timedelta(seconds=offset),
                keep=3,
            )

        backups = sorted(self.backup_dir.glob("*.db"))
        self.assertEqual(len(backups), 3)
        self.assertTrue(backups[0].name.endswith("000002Z.db"))
        self.assertTrue(backups[-1].name.endswith("000004Z.db"))

    def test_future_schema_revision_refuses_downgrade(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.execute("INSERT INTO alembic_version VALUES ('9999_future')")

        with self.assertRaises(UnsupportedSchemaVersion):
            migrate_database(self.db_path, self.backup_dir)

        self.assertEqual(current_revision(self.db_path), "9999_future")
        self.assertEqual(list(self.backup_dir.glob("*.db")), [])


if __name__ == "__main__":
    unittest.main()
