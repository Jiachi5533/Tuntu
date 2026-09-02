from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


class MigrationFailed(RuntimeError):
    pass


class UnsupportedSchemaVersion(RuntimeError):
    pass


_SCRIPT_LOCATION = Path(__file__).with_name("alembic")


def _config(database_path: Path | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    if database_path is not None:
        config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")
    return config


def latest_revision() -> str:
    revision = ScriptDirectory.from_config(_config()).get_current_head()
    if revision is None:
        raise RuntimeError("Alembic has no head revision")
    return revision


def _known_revisions() -> set[str]:
    return {
        revision.revision
        for revision in ScriptDirectory.from_config(_config()).walk_revisions()
    }


def current_revision(database_path: str | Path) -> str | None:
    path = Path(database_path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None


def create_backup(
    database_path: str | Path,
    backup_dir: str | Path,
    *,
    now: datetime | None = None,
    keep: int = 3,
) -> Path:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    source_path = Path(database_path)
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"tuntu-{timestamp}.db"
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    os.chmod(destination, 0o600)
    backups = sorted(destination_dir.glob("tuntu-*.db"), key=lambda path: path.name)
    for obsolete in backups[:-keep]:
        obsolete.unlink()
    return destination


def _run_alembic_upgrade(database_path: Path) -> None:
    command.upgrade(_config(database_path), "head")


def migrate_database(
    database_path: str | Path,
    backup_dir: str | Path,
    *,
    upgrade_runner: Callable[[Path], None] | None = None,
) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    existing = path.exists() and path.stat().st_size > 0
    revision = current_revision(path) if existing else None
    if revision is not None and revision not in _known_revisions():
        raise UnsupportedSchemaVersion(f"database revision {revision!r} is not supported")
    if revision == latest_revision():
        os.chmod(path, 0o600)
        return

    if existing:
        create_backup(path, backup_dir)

    descriptor, work_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".migrating", dir=path.parent
    )
    os.close(descriptor)
    work_path = Path(work_name)
    try:
        if existing:
            with sqlite3.connect(path) as source, sqlite3.connect(work_path) as target:
                source.backup(target)
        runner = upgrade_runner or _run_alembic_upgrade
        runner(work_path)
        if upgrade_runner is None and current_revision(work_path) != latest_revision():
            raise RuntimeError("migration did not reach the latest revision")
        os.replace(work_path, path)
        os.chmod(path, 0o600)
    except Exception as exc:
        work_path.unlink(missing_ok=True)
        raise MigrationFailed("database migration failed; original database was preserved") from exc
