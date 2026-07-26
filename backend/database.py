"""Synchronous SQLAlchemy setup for the fourth-week persistence layer."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.config import database_url


ALEMBIC_HEAD_REVISION = "0005_machine_query_ownership"


class Database:
    """Own one engine/session factory and configure SQLite defensively."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or database_url()
        parsed = make_url(self.url)
        connect_args: dict[str, object] = {}
        if parsed.drivername == "sqlite":
            connect_args["check_same_thread"] = False
            if parsed.database and parsed.database != ":memory:":
                Path(parsed.database).expanduser().resolve().parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
        self.engine = create_engine(
            self.url,
            connect_args=connect_args,
            future=True,
        )
        if parsed.drivername == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite_connection)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    def session(self) -> Session:
        return self.session_factory()

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def check_migration(self) -> None:
        """Fail fast when the API starts against an uninitialized schema."""

        try:
            with self.engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
        except Exception as exc:
            raise RuntimeError(
                "database schema is missing; run `python -m alembic upgrade head`"
            ) from exc
        if revision != ALEMBIC_HEAD_REVISION:
            raise RuntimeError(
                f"database revision is {revision!r}; "
                f"expected {ALEMBIC_HEAD_REVISION!r}"
            )

    def dispose(self) -> None:
        self.engine.dispose()


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


_lock = threading.RLock()
_database: Database | None = None


def get_database() -> Database:
    global _database
    with _lock:
        if _database is None:
            _database = Database()
        return _database


def set_database(database: Database | None) -> None:
    global _database
    with _lock:
        if _database is not None and _database is not database:
            _database.dispose()
        _database = database


def get_session() -> Iterator[Session]:
    session = get_database().session()
    try:
        yield session
    finally:
        session.close()


__all__ = [
    "ALEMBIC_HEAD_REVISION",
    "Database",
    "get_database",
    "get_session",
    "set_database",
]
