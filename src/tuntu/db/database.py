from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker


class DatabaseLocked(RuntimeError):
    pass


class Database:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 1_000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self.engine = create_engine(
            f"sqlite+pysqlite:///{self.path}",
            future=True,
            connect_args={
                "check_same_thread": False,
                "timeout": busy_timeout_ms / 1_000,
            },
        )
        self._install_sqlite_pragmas(self.engine, busy_timeout_ms)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @staticmethod
    def _install_sqlite_pragmas(engine: Engine, busy_timeout_ms: int) -> None:
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            if session.is_active:
                session.commit()
            else:
                session.rollback()
        except OperationalError as exc:
            session.rollback()
            if "database is locked" in str(exc).casefold():
                raise DatabaseLocked("SQLite database is locked") from exc
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
