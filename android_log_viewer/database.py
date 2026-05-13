from __future__ import annotations

import os
import sqlite3
import shutil
import tempfile
from typing import List, Optional

from .log_record import LogRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    pid       TEXT    NOT NULL,
    tid       TEXT    NOT NULL,
    level     TEXT    NOT NULL,
    tag       TEXT    NOT NULL,
    message   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_tag   ON logs(tag);
CREATE INDEX IF NOT EXISTS idx_ts    ON logs(timestamp);
"""


class LogDatabase:
    """SQLite store for log records. Lives on the main thread."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._temp_dir: Optional[str] = None
        if path is None:
            # Create a temporary file to keep RAM usage low.
            # This directory is removed in close().
            self._temp_dir = tempfile.mkdtemp(prefix="adb_log_viewer_")
            path = os.path.join(self._temp_dir, "logs.db")

        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in _SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s)
        self._conn.commit()

    # ------------------------------------------------------------------
    def insert_batch(self, records: List[LogRecord]) -> None:
        self._conn.executemany(
            "INSERT INTO logs (timestamp, pid, tid, level, tag, message)"
            " VALUES (?,?,?,?,?,?)",
            [(r.timestamp, r.pid, r.tid, r.level, r.tag, r.message) for r in records],
        )
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]

    def prune_oldest(self, count: int) -> None:
        """Remove the oldest 'count' records from the database."""
        if count <= 0:
            return
        # Use a subquery to find the IDs of the oldest rows.
        self._conn.execute(
            "DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id LIMIT ?)",
            (count,),
        )
        self._conn.commit()

    def clear(self) -> None:
        self._conn.execute("DELETE FROM logs")
        self._conn.execute(
            "DELETE FROM sqlite_sequence WHERE name='logs'"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    def save_to_file(self, path: str) -> None:
        dest = sqlite3.connect(path)
        self._conn.backup(dest)
        dest.close()

    def load_from_file(self, path: str) -> List[LogRecord]:
        src = sqlite3.connect(path)
        src.row_factory = sqlite3.Row
        rows = src.execute(
            "SELECT id, timestamp, pid, tid, level, tag, message FROM logs ORDER BY id"
        ).fetchall()
        src.close()
        return [LogRecord(r["id"], r["timestamp"], r["pid"], r["tid"],
                          r["level"], r["tag"], r["message"]) for r in rows]

    def size_bytes(self) -> int:
        """Approximate size of the log database in bytes."""
        try:
            if self._path and os.path.exists(self._path):
                return os.path.getsize(self._path)
            # fallback for :memory:
            pages     = self._conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
            return pages * page_size
        except Exception:
            return 0

    def close(self) -> None:
        self._conn.close()
        if self._temp_dir and os.path.exists(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir)
            except Exception:
                pass
