"""
src.state.manager
~~~~~~~~~~~~~~~~~
SQLite-backed state manager for idempotent pipeline execution.

What it tracks
--------------
processed_files : one row per successfully processed input file,
                  keyed by SHA-256 hash of the file content.
pipeline_runs   : audit log of every pipeline execution with
                  start/end timestamps and a JSON summary.

Why SHA-256 of content?
    File name alone is insufficient — the same file name could be
    re-delivered with different content (e.g. a corrected re-send).
    Hashing the content ensures we re-process if and only if the data changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS processed_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT    NOT NULL,
    file_hash       TEXT    NOT NULL UNIQUE,
    processed_at    TEXT    NOT NULL,
    record_count    INTEGER DEFAULT 0,
    valid_count     INTEGER DEFAULT 0,
    quarantine_count INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT    NOT NULL UNIQUE,
    started_at   TEXT    NOT NULL,
    completed_at TEXT,
    status       TEXT    DEFAULT 'RUNNING',
    summary_json TEXT
);
"""


class StateManager:
    """Manages pipeline state for idempotent, re-runnable execution."""

    DB_FILE = "pipeline_state.db"

    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = state_dir / self.DB_FILE
        self._init_db()
        logger.debug("State DB: %s", self._db_path)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # File-level idempotency
    # ------------------------------------------------------------------

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """SHA-256 of the entire file content (streaming, memory-safe)."""
        h = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                h.update(chunk)
        return h.hexdigest()

    def is_file_processed(self, file_hash: str) -> bool:
        """True if a file with this content hash has already been processed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM processed_files WHERE file_hash = ?",
                (file_hash,),
            ).fetchone()
        return row is not None

    def mark_file_processed(
        self,
        file_name: str,
        file_hash: str,
        record_count: int = 0,
        valid_count: int = 0,
        quarantine_count: int = 0,
        duplicate_count: int = 0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_files
                    (file_name, file_hash, processed_at,
                     record_count, valid_count, quarantine_count, duplicate_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_name,
                    file_hash,
                    datetime.now(timezone.utc).isoformat(),
                    record_count,
                    valid_count,
                    quarantine_count,
                    duplicate_count,
                ),
            )
        logger.info(
            "State: marked '%s' as processed (hash %s…)",
            file_name, file_hash[:12],
        )

    def get_processed_files(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM processed_files ORDER BY processed_at"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Run-level audit log
    # ------------------------------------------------------------------

    def create_run(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
                (run_id, datetime.now(timezone.utc).isoformat()),
            )

    def complete_run(self, run_id: str, summary: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET completed_at = ?, status = 'COMPLETED', summary_json = ?
                WHERE run_id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(summary, default=str),
                    run_id,
                ),
            )
