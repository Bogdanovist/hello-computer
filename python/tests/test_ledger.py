"""Tests for the vox.ledger module.

Covers core operations (T015) and CRUD/export/import (T016).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vox.ledger import (
    Ledger,
    calculate_confidence,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db_path(tmp_path):
    """Return a temporary database file path."""
    return tmp_path / "test_corrections.db"


@pytest.fixture()
def ledger(db_path):
    """Create a fresh unencrypted Ledger for testing."""
    led = Ledger(db_path, encryption_key=None)
    yield led
    led.close()


@pytest.fixture()
def freeze_time(monkeypatch):
    """Pin vox.ledger._now() to _FIXED_NOW; returns an advance(days=) helper."""
    current = [_FIXED_NOW]

    def _frozen_now():
        return current[0]

    monkeypatch.setattr("vox.ledger._now", _frozen_now)

    def advance(*, days=0, seconds=0):
        current[0] = current[0] + timedelta(days=days, seconds=seconds)

    return advance


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


class TestInsertCorrection:
    def test_insert_new_correction(self, ledger):
        """Insert a correction pair, verify it's stored with correct fields."""
        row_id = ledger.insert_correction(
            injected_text="I need to update the see lie tool",
            corrected_text="I need to update the CLI tool",
            diff_pairs=[("see lie", "CLI")],
            app_bundle_id="com.apple.Terminal",
        )

        assert row_id is not None
        corrections = ledger.list_corrections()
        assert len(corrections) == 1

        rec = corrections[0]
        assert rec.id == row_id
        assert rec.injected_text == "I need to update the see lie tool"
        assert rec.corrected_text == "I need to update the CLI tool"
        assert rec.diff_pairs == [["see lie", "CLI"]]
        assert rec.app_bundle_id == "com.apple.Terminal"
        assert rec.times_seen == 1
        assert rec.active is True


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_same_diff_pairs_increments_times_seen(self, ledger):
        """Insert same diff pair twice -> single row with times_seen=2."""
        id1 = ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )
        id2 = ledger.insert_correction(
            injected_text="the see lie",
            corrected_text="the CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        assert id1 == id2
        corrections = ledger.list_corrections()
        assert len(corrections) == 1
        assert corrections[0].times_seen == 2


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_confidence_times_seen_1_with_recency(self, freeze_time):
        """times_seen=1, last seen today -> 0.3 + 0.07 + 0.2 = 0.57."""
        result = calculate_confidence(1, _FIXED_NOW)
        assert result == pytest.approx(0.57, abs=1e-9)

    def test_confidence_times_seen_3_with_recency(self, freeze_time):
        """times_seen=3, last seen today -> 0.3 + 0.21 + 0.2 = 0.71."""
        result = calculate_confidence(3, _FIXED_NOW)
        assert result == pytest.approx(0.71, abs=1e-9)

    def test_confidence_times_seen_7_with_recency(self, freeze_time):
        """times_seen=7, last seen today -> 0.3 + 0.49 + 0.2 = 0.99."""
        result = calculate_confidence(7, _FIXED_NOW)
        assert result == pytest.approx(0.99, abs=1e-9)

    def test_frequency_bonus_capped(self, freeze_time):
        """Frequency bonus caps at 0.49; very high times_seen yields 0.99."""
        result = calculate_confidence(100, _FIXED_NOW)
        # 0.3 + min(0.49, 7.0) + 0.2 = 0.99
        assert result == pytest.approx(0.99, abs=1e-9)
        assert result <= 1.0


# ---------------------------------------------------------------------------
# Recency decay
# ---------------------------------------------------------------------------


class TestRecencyDecay:
    def test_recency_bonus_within_7_days(self, freeze_time):
        """Last seen 5 days ago -> recency_bonus = 0.2."""
        five_days_ago = _FIXED_NOW - timedelta(days=5)
        result = calculate_confidence(1, five_days_ago)
        assert result == pytest.approx(0.57, abs=1e-9)

    def test_recency_bonus_within_30_days(self, freeze_time):
        """Last seen 15 days ago -> recency_bonus = 0.1."""
        fifteen_days_ago = _FIXED_NOW - timedelta(days=15)
        result = calculate_confidence(1, fifteen_days_ago)
        assert result == pytest.approx(0.47, abs=1e-9)

    def test_recency_bonus_beyond_30_days(self, freeze_time):
        """Last seen 60 days ago -> recency_bonus = 0.0."""
        sixty_days_ago = _FIXED_NOW - timedelta(days=60)
        result = calculate_confidence(1, sixty_days_ago)
        assert result == pytest.approx(0.37, abs=1e-9)

    def test_decay_ordering(self, freeze_time):
        """Confidence decreases as time since last seen increases."""
        recent = calculate_confidence(3, _FIXED_NOW)
        mid = calculate_confidence(3, _FIXED_NOW - timedelta(days=15))
        old = calculate_confidence(3, _FIXED_NOW - timedelta(days=60))
        assert recent > mid > old


# ---------------------------------------------------------------------------
# Query — ranking
# ---------------------------------------------------------------------------


class TestQueryRanking:
    def test_higher_confidence_ranks_first(self, ledger, freeze_time):
        """Higher-confidence correction ranks first among fuzzy matches."""
        # "see lie" -> "CLI" inserted 3 times -> times_seen=3, confidence=0.71
        for _ in range(3):
            ledger.insert_correction(
                injected_text="see lie",
                corrected_text="CLI",
                diff_pairs=[("see lie", "CLI")],
            )

        # Different correction inserted once -> times_seen=1, confidence=0.57
        ledger.insert_correction(
            injected_text="the see lie tool",
            corrected_text="the CLI tool",
            diff_pairs=[("see lie tool", "CLI tool")],
        )

        results = ledger.query_relevant_corrections("see lie")
        assert len(results) >= 1
        assert results[0].diff_pairs == [["see lie", "CLI"]]
        assert results[0].times_seen == 3


# ---------------------------------------------------------------------------
# Query — empty ledger
# ---------------------------------------------------------------------------


class TestQueryEmptyLedger:
    def test_query_empty_ledger_returns_empty_list(self, ledger):
        """Querying an empty ledger returns an empty list (no error)."""
        results = ledger.query_relevant_corrections("anything")
        assert results == []


# ---------------------------------------------------------------------------
# Query — min_confidence filter
# ---------------------------------------------------------------------------


class TestMinConfidenceFilter:
    def test_excludes_low_confidence(self, ledger, freeze_time):
        """Corrections below min_confidence are excluded from results."""
        # Insert once -> times_seen=1, confidence=0.57
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        # High threshold excludes the correction
        results = ledger.query_relevant_corrections(
            "see lie", min_confidence=0.8,
        )
        assert len(results) == 0

        # Lower threshold includes it
        results = ledger.query_relevant_corrections(
            "see lie", min_confidence=0.5,
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Query — app context boost
# ---------------------------------------------------------------------------


class TestAppContextBoost:
    def test_same_app_ranks_higher(self, ledger, freeze_time):
        """Correction from the queried app ranks above one from a different app."""
        # Correction A: different app, times_seen=3
        for _ in range(3):
            ledger.insert_correction(
                injected_text="open grafana please",
                corrected_text="open Grafana please",
                diff_pairs=[("grafana", "Grafana")],
                app_bundle_id="com.other.app",
            )

        # Correction B: target app, times_seen=3
        for _ in range(3):
            ledger.insert_correction(
                injected_text="open grafana now",
                corrected_text="open Grafana now",
                diff_pairs=[("grafana now", "Grafana now")],
                app_bundle_id="com.apple.Terminal",
            )

        # Query from Terminal — Terminal correction gets +0.1 app boost
        results = ledger.query_relevant_corrections(
            "grafana", app_bundle_id="com.apple.Terminal",
        )
        assert len(results) == 2
        assert results[0].app_bundle_id == "com.apple.Terminal"


# ---------------------------------------------------------------------------
# Disable excludes from queries (T016)
# ---------------------------------------------------------------------------


class TestDisableExcludesFromQueries:
    def test_disabled_correction_excluded_from_queries(self, ledger, freeze_time):
        """Disable a correction, verify it no longer appears in query results."""
        for _ in range(3):
            ledger.insert_correction(
                injected_text="see lie",
                corrected_text="CLI",
                diff_pairs=[("see lie", "CLI")],
            )

        results_before = ledger.query_relevant_corrections("see lie")
        assert len(results_before) == 1

        ledger.disable_correction(results_before[0].id)

        results_after = ledger.query_relevant_corrections("see lie")
        assert len(results_after) == 0

    def test_disabled_correction_excluded_from_list(self, ledger):
        """Disable a correction, verify list_corrections omits it."""
        row_id = ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        assert len(ledger.list_corrections()) == 1
        ledger.disable_correction(row_id)
        assert len(ledger.list_corrections()) == 0


# ---------------------------------------------------------------------------
# Enable re-includes (T016)
# ---------------------------------------------------------------------------


class TestEnableReIncludes:
    def test_re_enabled_correction_included_in_queries(self, ledger, freeze_time):
        """Re-enable a disabled correction, verify it reappears in queries."""
        for _ in range(3):
            ledger.insert_correction(
                injected_text="see lie",
                corrected_text="CLI",
                diff_pairs=[("see lie", "CLI")],
            )

        corrections = ledger.list_corrections()
        row_id = corrections[0].id

        ledger.disable_correction(row_id)
        assert len(ledger.query_relevant_corrections("see lie")) == 0

        ledger.enable_correction(row_id)
        results = ledger.query_relevant_corrections("see lie")
        assert len(results) == 1
        assert results[0].id == row_id

    def test_re_enabled_correction_included_in_list(self, ledger):
        """Re-enable a disabled correction, verify list_corrections shows it."""
        row_id = ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        ledger.disable_correction(row_id)
        assert len(ledger.list_corrections()) == 0

        ledger.enable_correction(row_id)
        assert len(ledger.list_corrections()) == 1


# ---------------------------------------------------------------------------
# Delete removes permanently (T016)
# ---------------------------------------------------------------------------


class TestDeleteRemovesPermanently:
    def test_delete_removes_correction(self, ledger):
        """Delete a correction, verify it's gone from the database entirely."""
        row_id = ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        ledger.delete_correction(row_id)

        # Not in list_corrections (active only)
        assert len(ledger.list_corrections()) == 0

        # Also not in the raw database at all
        cursor = ledger.connection.execute(
            "SELECT COUNT(*) FROM corrections WHERE id = ?", (row_id,),
        )
        assert cursor.fetchone()[0] == 0

    def test_delete_does_not_affect_other_corrections(self, ledger):
        """Deleting one correction leaves others intact."""
        id1 = ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )
        id2 = ledger.insert_correction(
            injected_text="grafana",
            corrected_text="Grafana",
            diff_pairs=[("grafana", "Grafana")],
        )

        ledger.delete_correction(id1)

        remaining = ledger.list_corrections()
        assert len(remaining) == 1
        assert remaining[0].id == id2


# ---------------------------------------------------------------------------
# Export produces valid JSON (T016)
# ---------------------------------------------------------------------------


class TestExportProducesValidJSON:
    def test_export_valid_json_with_all_fields(self, ledger, freeze_time):
        """Export all corrections, verify valid JSON with expected fields."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
            app_bundle_id="com.apple.Terminal",
        )
        ledger.insert_correction(
            injected_text="grafana",
            corrected_text="Grafana",
            diff_pairs=[("grafana", "Grafana")],
        )

        exported = ledger.export_json()
        data = json.loads(exported)

        assert isinstance(data, list)
        assert len(data) == 2

        expected_fields = {
            "created_at", "updated_at", "app_bundle_id",
            "raw_transcript", "injected_text", "corrected_text",
            "diff_pairs", "times_seen", "confidence", "active",
        }
        for entry in data:
            assert set(entry.keys()) == expected_fields

    def test_export_excludes_id_field(self, ledger):
        """Exported JSON must not contain the id field."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        data = json.loads(ledger.export_json())
        for entry in data:
            assert "id" not in entry

    def test_export_includes_disabled_corrections(self, ledger):
        """Export includes both active and disabled corrections."""
        id1 = ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )
        ledger.insert_correction(
            injected_text="grafana",
            corrected_text="Grafana",
            diff_pairs=[("grafana", "Grafana")],
        )
        ledger.disable_correction(id1)

        data = json.loads(ledger.export_json())
        assert len(data) == 2

        active_flags = {entry["active"] for entry in data}
        assert active_flags == {True, False}


# ---------------------------------------------------------------------------
# Import with new entries (T016)
# ---------------------------------------------------------------------------


class TestImportNewEntries:
    def test_import_new_corrections(self, ledger, freeze_time):
        """Import corrections from JSON, verify they appear in the ledger."""
        import_data = json.dumps([
            {
                "created_at": "2026-02-25 10:00:00",
                "updated_at": "2026-02-25 10:00:00",
                "app_bundle_id": "com.apple.Terminal",
                "raw_transcript": "see lie",
                "injected_text": "see lie",
                "corrected_text": "CLI",
                "diff_pairs": [["see lie", "CLI"]],
                "times_seen": 3,
                "confidence": 0.71,
                "active": True,
            },
            {
                "created_at": "2026-02-25 11:00:00",
                "updated_at": "2026-02-25 11:00:00",
                "app_bundle_id": None,
                "raw_transcript": "grafana",
                "injected_text": "grafana",
                "corrected_text": "Grafana",
                "diff_pairs": [["grafana", "Grafana"]],
                "times_seen": 1,
                "confidence": 0.5,
                "active": True,
            },
        ])

        count = ledger.import_json(import_data)
        assert count == 2

        corrections = ledger.list_corrections()
        assert len(corrections) == 2

    def test_import_returns_correct_count(self, ledger):
        """import_json returns the number of entries processed."""
        import_data = json.dumps([
            {
                "injected_text": "see lie",
                "corrected_text": "CLI",
                "diff_pairs": [["see lie", "CLI"]],
                "times_seen": 1,
                "created_at": "2026-02-25 10:00:00",
                "updated_at": "2026-02-25 10:00:00",
            },
        ])
        assert ledger.import_json(import_data) == 1


# ---------------------------------------------------------------------------
# Import merge conflict — higher times_seen wins (T016)
# ---------------------------------------------------------------------------


class TestImportMergeConflict:
    def test_merge_keeps_higher_times_seen(self, ledger, freeze_time):
        """Import with existing diff pairs: higher times_seen wins."""
        # Insert locally with times_seen=1
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        # Import with times_seen=5 for the same diff pair
        import_data = json.dumps([
            {
                "created_at": "2026-02-20 10:00:00",
                "updated_at": "2026-02-26 12:00:00",
                "app_bundle_id": None,
                "raw_transcript": "see lie",
                "injected_text": "see lie",
                "corrected_text": "CLI",
                "diff_pairs": [["see lie", "CLI"]],
                "times_seen": 5,
                "confidence": 0.65,
                "active": True,
            },
        ])

        count = ledger.import_json(import_data)
        assert count == 1

        # Should still be a single row
        corrections = ledger.list_corrections()
        assert len(corrections) == 1
        assert corrections[0].times_seen == 5

    def test_merge_keeps_local_when_higher(self, ledger, freeze_time):
        """Import with lower times_seen: local higher value preserved."""
        # Insert locally 4 times -> times_seen=4
        for _ in range(4):
            ledger.insert_correction(
                injected_text="see lie",
                corrected_text="CLI",
                diff_pairs=[("see lie", "CLI")],
            )

        # Import with times_seen=2
        import_data = json.dumps([
            {
                "created_at": "2026-02-20 10:00:00",
                "updated_at": "2026-02-20 10:00:00",
                "app_bundle_id": None,
                "raw_transcript": "see lie",
                "injected_text": "see lie",
                "corrected_text": "CLI",
                "diff_pairs": [["see lie", "CLI"]],
                "times_seen": 2,
                "confidence": 0.5,
                "active": True,
            },
        ])

        ledger.import_json(import_data)

        corrections = ledger.list_corrections()
        assert len(corrections) == 1
        assert corrections[0].times_seen == 4

    def test_merge_keeps_more_recent_updated_at(self, ledger, freeze_time):
        """Import merge: more recent updated_at wins."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        # Import with a more recent updated_at
        import_data = json.dumps([
            {
                "created_at": "2026-02-20 10:00:00",
                "updated_at": "2026-02-27 00:00:00",
                "app_bundle_id": None,
                "raw_transcript": "see lie",
                "injected_text": "see lie",
                "corrected_text": "CLI",
                "diff_pairs": [["see lie", "CLI"]],
                "times_seen": 1,
                "confidence": 0.5,
                "active": True,
            },
        ])

        ledger.import_json(import_data)

        # Verify updated_at took the more recent value
        cursor = ledger.connection.execute(
            "SELECT updated_at FROM corrections",
        )
        updated_at = cursor.fetchone()[0]
        assert updated_at == "2026-02-27 00:00:00"

    def test_merge_does_not_create_duplicates(self, ledger, freeze_time):
        """Import with existing diff pairs must not create duplicate rows."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        import_data = json.dumps([
            {
                "created_at": "2026-02-25 10:00:00",
                "updated_at": "2026-02-25 10:00:00",
                "raw_transcript": "see lie",
                "injected_text": "see lie",
                "corrected_text": "CLI",
                "diff_pairs": [["see lie", "CLI"]],
                "times_seen": 1,
            },
        ])

        ledger.import_json(import_data)

        cursor = ledger.connection.execute(
            "SELECT COUNT(*) FROM corrections",
        )
        assert cursor.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Reset clears DB and creates backup (T016)
# ---------------------------------------------------------------------------


class TestResetClearsDBAndCreatesBackup:
    def test_reset_clears_all_corrections(self, ledger, freeze_time):
        """After reset, the database is empty."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )
        ledger.insert_correction(
            injected_text="grafana",
            corrected_text="Grafana",
            diff_pairs=[("grafana", "Grafana")],
        )

        ledger.reset()

        assert len(ledger.list_corrections()) == 0

        cursor = ledger.connection.execute(
            "SELECT COUNT(*) FROM corrections",
        )
        assert cursor.fetchone()[0] == 0

    def test_reset_creates_backup_file(self, ledger, db_path, freeze_time):
        """Reset creates a JSON backup file in the same directory."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )

        backup_path = ledger.reset()

        assert backup_path.exists()
        assert backup_path.parent == db_path.parent
        assert "corrections_backup_" in backup_path.name
        assert backup_path.suffix == ".json"

    def test_reset_backup_contains_corrections(self, ledger, freeze_time):
        """Backup file is valid JSON containing the corrections."""
        ledger.insert_correction(
            injected_text="see lie",
            corrected_text="CLI",
            diff_pairs=[("see lie", "CLI")],
        )
        ledger.insert_correction(
            injected_text="grafana",
            corrected_text="Grafana",
            diff_pairs=[("grafana", "Grafana")],
        )

        backup_path = ledger.reset()

        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        assert isinstance(backup_data, list)
        assert len(backup_data) == 2
