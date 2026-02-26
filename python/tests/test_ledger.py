"""Tests for the vox.ledger module — core operations (T015)."""

from __future__ import annotations

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
