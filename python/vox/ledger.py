"""Correction ledger — persistent storage for correction pairs.

The ledger stores user corrections in an encrypted SQLite database (SQLCipher
when available, plain sqlite3 for testing / non-macOS environments). It supports
CRUD operations, deduplication, confidence scoring, and relevance-ranked queries.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
    diff_pairs_normalized TEXT NOT NULL DEFAULT '',
    times_seen INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.5,
    active INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_confidence ON corrections(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_active ON corrections(active);
CREATE INDEX IF NOT EXISTS idx_app ON corrections(app_bundle_id);
CREATE INDEX IF NOT EXISTS idx_diff_pairs_normalized
    ON corrections(diff_pairs_normalized);
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


def _now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _normalize_diff_pairs(diff_pairs: list[tuple[str, str]]) -> str:
    """Return a canonical JSON string for deduplication comparison.

    Pairs are sorted and lowercased so that ordering and case differences
    do not create duplicate rows.
    """
    normalized = sorted((a.lower(), b.lower()) for a, b in diff_pairs)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def calculate_confidence(times_seen: int, last_seen: datetime) -> float:
    """Compute confidence score from frequency and recency.

    Formula:
        base (0.3)
      + frequency_bonus: min(0.49, 0.07 * times_seen)
      + recency_bonus:   0.2 if ≤7 days, 0.1 if ≤30 days, else 0.0

    Result is capped at 1.0.
    """
    base = 0.3
    frequency_bonus = min(0.49, 0.07 * times_seen)

    now = _now()
    # If last_seen is naive, assume UTC.
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    days_since = (now - last_seen).days

    if days_since <= 7:
        recency_bonus = 0.2
    elif days_since <= 30:
        recency_bonus = 0.1
    else:
        recency_bonus = 0.0

    return min(1.0, base + frequency_bonus + recency_bonus)


def get_current_confidence(record: CorrectionRecord) -> float:
    """Recalculate confidence for *record* using the current time."""
    return calculate_confidence(record.times_seen, record.updated_at)


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

    # ------------------------------------------------------------------
    # Insert / deduplication
    # ------------------------------------------------------------------

    def insert_correction(
        self,
        injected_text: str,
        corrected_text: str,
        diff_pairs: list[tuple[str, str]],
        app_bundle_id: str | None = None,
    ) -> int:
        """Insert a correction or increment ``times_seen`` if it already exists.

        Deduplication is based on the *normalized* ``diff_pairs`` (sorted,
        lowercased).  If a matching row is found its ``times_seen`` is bumped
        and ``updated_at`` refreshed.  Otherwise a new row is created.

        Returns the ``id`` of the inserted or updated row.
        """
        assert self._conn is not None
        normalized = _normalize_diff_pairs(diff_pairs)

        # Look for an existing row with the same normalized diff_pairs.
        cursor = self._conn.execute(
            "SELECT id, times_seen FROM corrections WHERE diff_pairs_normalized = ?",
            (normalized,),
        )
        row = cursor.fetchone()

        now_str = _now().strftime("%Y-%m-%d %H:%M:%S")

        if row is not None:
            row_id, times_seen = row
            new_times_seen = times_seen + 1
            new_confidence = calculate_confidence(
                new_times_seen, _now(),
            )
            self._conn.execute(
                "UPDATE corrections"
                " SET times_seen = ?, updated_at = ?,"
                " confidence = ? WHERE id = ?",
                (new_times_seen, now_str, new_confidence, row_id),
            )
            self._conn.commit()
            return row_id

        # New correction.
        confidence = calculate_confidence(1, _now())
        cursor = self._conn.execute(
            """INSERT INTO corrections
               (raw_transcript, injected_text, corrected_text,
                diff_pairs, diff_pairs_normalized,
                app_bundle_id, times_seen, confidence, active,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?)""",
            (
                injected_text,
                injected_text,
                corrected_text,
                json.dumps(diff_pairs, ensure_ascii=False),
                normalized,
                app_bundle_id,
                confidence,
                now_str,
                now_str,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
