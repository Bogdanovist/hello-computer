"""Correction ledger — persistent storage for correction pairs.

The ledger stores user corrections in an encrypted SQLite database (SQLCipher
when available, plain sqlite3 for testing / non-macOS environments). It supports
CRUD operations, deduplication, confidence scoring, and relevance-ranked queries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore[import-untyped]
except ImportError:
    sqlcipher = None  # type: ignore[assignment]


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    app_bundle_id TEXT,
    raw_transcript TEXT NOT NULL,
    injected_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    diff_pairs TEXT NOT NULL,
    times_seen INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.5,
    active INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_confidence ON corrections(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_active ON corrections(active);
CREATE INDEX IF NOT EXISTS idx_app ON corrections(app_bundle_id);
"""


@dataclass
class CorrectionRecord:
    """A single correction entry from the ledger."""

    id: int
    created_at: datetime
    updated_at: datetime
    app_bundle_id: str | None
    raw_transcript: str
    injected_text: str
    corrected_text: str
    diff_pairs: list[tuple[str, str]]
    times_seen: int
    confidence: float
    active: bool


class Ledger:
    """Persistent correction ledger backed by SQLite / SQLCipher.

    Parameters
    ----------
    db_path:
        Path to the database file.
    encryption_key:
        When provided *and* pysqlcipher3 is installed, opens an encrypted
        SQLCipher database. When ``None``, uses plain sqlite3 (suitable for
        testing and non-macOS environments).
    """

    def __init__(self, db_path: str | Path, encryption_key: str | None = None) -> None:
        self._db_path = Path(db_path)
        self._encryption_key = encryption_key
        self._conn: sqlite3.Connection | None = None
        self._connect()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        if self._encryption_key is not None and sqlcipher is not None:
            self._conn = sqlcipher.connect(str(self._db_path))
            self._conn.execute(f"PRAGMA key = '{self._encryption_key}'")
        else:
            self._conn = sqlite3.connect(str(self._db_path))

        # Enable WAL mode for robustness.
        self._conn.execute("PRAGMA journal_mode = WAL")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the underlying database connection."""
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
