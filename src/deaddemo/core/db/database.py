"""SQLite connection management and schema migrations.

One ``Database`` per process. All writes should happen from a single thread (the GUI main
thread, or the CLI); worker threads may open read-only connections via ``open_readonly``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from deaddemo import paths

SCHEMA_VERSION = 2  # 2: match_player_extras, player_notes (schema.sql is idempotent, so re-running it migrates)
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, conn: sqlite3.Connection, path: Path | None):
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, path: Path | None = None) -> Database:
        path = path or paths.db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        db = cls(conn, path)
        db._configure()
        db.migrate()
        return db

    @classmethod
    def open_memory(cls) -> Database:
        db = cls(sqlite3.connect(":memory:"), None)
        db._configure()
        db.migrate()
        return db

    @classmethod
    def open_readonly(cls, path: Path | None = None) -> Database:
        path = path or paths.db_path()
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return cls(conn, path)

    def _configure(self) -> None:
        self.conn.row_factory = sqlite3.Row
        if self.path is not None:
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    def migrate(self) -> None:
        self.conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
        current = self.schema_version()
        if current is None or current < SCHEMA_VERSION:
            # Future migrations go here, keyed on `current`.
            with self.transaction():
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    def schema_version(self) -> int | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()
