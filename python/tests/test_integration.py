"""Integration tests for the full Python pipeline (T037).

Tests the end-to-end flow: diff engine → ledger insert → ledger query →
post-processor, verifying that corrections accumulate and become auto-applicable
after sufficient repetition.  Ollama calls are mocked — no running instance
required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from vox.config import VoxConfig
from vox.diff_engine import extract_diff_pairs
from vox.ledger import Ledger
from vox.post_processor import post_process

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


@pytest.fixture()
def config(tmp_path, monkeypatch):
    """Return a VoxConfig with isolated paths."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"
    monkeypatch.setattr("vox.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("vox.config.CONFIG_FILE", config_file)
    return VoxConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Realistic correction scenarios — dictation errors Whisper commonly makes.
_CORRECTION_SCENARIOS = [
    # (injected_text, corrected_text, expected_pair_substring)
    (
        "I need to update the to you I component",
        "I need to update the TUI component",
        ("to you I", "TUI"),
    ),
    (
        "please check the see lie output",
        "please check the CLI output",
        ("see lie", "CLI"),
    ),
    (
        "send the event to posthog",
        "send the event to PostHog",
        ("posthog", "PostHog"),
    ),
    (
        "run docker compose up",
        "run docker-compose up",
        ("docker compose", "docker-compose"),
    ),
]


def _simulate_correction_cycle(
    ledger: Ledger,
    injected_text: str,
    corrected_text: str,
    app_bundle_id: str | None = None,
) -> int:
    """Simulate one inject → diff → ledger insert cycle.

    Returns the correction row ID.
    """
    diff_pairs = extract_diff_pairs(injected_text, corrected_text)
    assert diff_pairs, (
        f"Expected diff pairs from: {injected_text!r} → {corrected_text!r}"
    )
    return ledger.insert_correction(
        injected_text=injected_text,
        corrected_text=corrected_text,
        diff_pairs=diff_pairs,
        app_bundle_id=app_bundle_id,
    )


# ---------------------------------------------------------------------------
# Tests: Full pipeline — correction accumulation
# ---------------------------------------------------------------------------


class TestCorrectionAccumulation:
    """Simulate repeated correction cycles and verify the learning loop."""

    def test_four_cycles_then_query_returns_corrections(
        self, ledger, freeze_time, config,
    ):
        """After 4 identical correction cycles, the 5th query should return
        auto-applicable corrections (times_seen >= 3, confidence above
        the default threshold of 0.5).
        """
        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"

        # Simulate 4 correction cycles for the same error.
        for i in range(4):
            _simulate_correction_cycle(ledger, injected, corrected)
            # Advance time slightly between cycles.
            freeze_time(seconds=60)

        # Query the ledger with a new transcript containing the same error.
        query_transcript = "please fix the to you I settings"
        results = ledger.query_relevant_corrections(
            raw_transcript=query_transcript,
            min_confidence=config.post_processing.confidence_threshold,
        )

        assert len(results) >= 1
        # The correction should have times_seen=4 (4 cycles, deduplicated).
        top = results[0]
        assert top.times_seen == 4
        # Confidence for times_seen=4, recent: 0.3 + min(0.49, 0.28) + 0.2 = 0.78
        assert top.confidence >= config.post_processing.confidence_threshold

    def test_multiple_distinct_corrections_accumulate(
        self, ledger, freeze_time,
    ):
        """Multiple distinct correction patterns each accumulate independently."""
        for scenario in _CORRECTION_SCENARIOS:
            injected, corrected, _ = scenario
            # Insert each correction 3 times to cross the threshold.
            for _ in range(3):
                _simulate_correction_cycle(ledger, injected, corrected)
                freeze_time(seconds=30)

        # Query with a transcript that might match several corrections.
        results = ledger.query_relevant_corrections(
            raw_transcript="update the to you I and check the see lie",
            min_confidence=0.5,
        )

        # Should find at least the TUI and CLI corrections.
        found_pairs = set()
        for r in results:
            for pair in r.diff_pairs:
                found_pairs.add(tuple(pair))

        assert ("to you I", "TUI") in found_pairs
        assert ("see lie", "CLI") in found_pairs

    def test_single_occurrence_below_threshold(self, ledger, freeze_time):
        """A correction seen only once should have confidence below the
        default retrieval threshold when recency bonus expires.
        """
        injected = "check the see lie output"
        corrected = "check the CLI output"
        _simulate_correction_cycle(ledger, injected, corrected)

        # Advance past recency bonus window (>30 days).
        freeze_time(days=31)

        results = ledger.query_relevant_corrections(
            raw_transcript="run the see lie",
            min_confidence=0.5,
        )

        # times_seen=1, no recency: 0.3 + 0.07 + 0.0 = 0.37 — below 0.5
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Tests: Post-processor with real ledger data
# ---------------------------------------------------------------------------


class TestPostProcessWithLedger:
    """Test the post_process function using a real Ledger with accumulated data."""

    def test_post_process_applies_corrections(
        self, ledger, freeze_time, config,
    ):
        """After ledger has sufficient data, post_process with mocked Ollama
        should return the corrected transcript.
        """
        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"

        # Build up correction history (4 times → high confidence).
        for _ in range(4):
            _simulate_correction_cycle(ledger, injected, corrected)
            freeze_time(seconds=30)

        raw_transcript = "please fix the to you I settings"
        expected_output = "please fix the TUI settings"

        with patch("vox.post_processor.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": expected_output}
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            result = post_process(
                raw_transcript=raw_transcript,
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == expected_output

        # Verify Ollama was called with a prompt containing the correction.
        call_args = mock_post.call_args
        request_body = call_args.kwargs["json"]
        prompt = request_body["prompt"]
        assert "to you I" in prompt
        assert "TUI" in prompt

    def test_post_process_skips_empty_ledger(self, ledger, config):
        """With an empty ledger, post_process returns raw transcript without
        calling Ollama.
        """
        raw_transcript = "some raw transcript text"

        with patch("vox.post_processor.requests.post") as mock_post:
            result = post_process(
                raw_transcript=raw_transcript,
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == raw_transcript
        mock_post.assert_not_called()

    def test_post_process_skips_low_confidence_corrections(
        self, ledger, freeze_time, config,
    ):
        """Corrections below confidence threshold should not trigger Ollama."""
        injected = "check the see lie output"
        corrected = "check the CLI output"
        # Insert only once.
        _simulate_correction_cycle(ledger, injected, corrected)

        # Advance past recency bonus window.
        freeze_time(days=31)

        with patch("vox.post_processor.requests.post") as mock_post:
            result = post_process(
                raw_transcript="run the see lie",
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == "run the see lie"
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Graceful degradation — Ollama down
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Verify that the system degrades gracefully when Ollama is unavailable."""

    def test_ollama_connection_refused_returns_raw(
        self, ledger, freeze_time, config,
    ):
        """When Ollama is not running (ConnectionError), post_process
        returns the raw transcript unchanged.
        """
        import requests as req

        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"

        # Build correction history so ledger query returns results.
        for _ in range(4):
            _simulate_correction_cycle(ledger, injected, corrected)
            freeze_time(seconds=30)

        raw_transcript = "please fix the to you I settings"

        with patch(
            "vox.post_processor.requests.post",
            side_effect=req.ConnectionError("Connection refused"),
        ):
            result = post_process(
                raw_transcript=raw_transcript,
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == raw_transcript

    def test_ollama_timeout_returns_raw(
        self, ledger, freeze_time, config,
    ):
        """When Ollama times out, post_process returns the raw transcript."""
        import requests as req

        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"

        for _ in range(4):
            _simulate_correction_cycle(ledger, injected, corrected)
            freeze_time(seconds=30)

        raw_transcript = "please fix the to you I settings"

        with patch(
            "vox.post_processor.requests.post",
            side_effect=req.Timeout("Request timed out"),
        ):
            result = post_process(
                raw_transcript=raw_transcript,
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == raw_transcript

    def test_ollama_hallucination_returns_raw(
        self, ledger, freeze_time, config,
    ):
        """When Ollama returns output that is >50% different from input
        (hallucination), post_process falls back to raw transcript.
        """
        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"

        for _ in range(4):
            _simulate_correction_cycle(ledger, injected, corrected)
            freeze_time(seconds=30)

        raw_transcript = "please fix the to you I settings"
        hallucinated = (
            "The quick brown fox jumps over the lazy dog unrelated"
        )

        with patch("vox.post_processor.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": hallucinated}
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            result = post_process(
                raw_transcript=raw_transcript,
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == raw_transcript

    def test_ollama_empty_response_returns_raw(
        self, ledger, freeze_time, config,
    ):
        """When Ollama returns an empty response, post_process returns raw."""
        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"

        for _ in range(4):
            _simulate_correction_cycle(ledger, injected, corrected)
            freeze_time(seconds=30)

        raw_transcript = "please fix the to you I settings"

        with patch("vox.post_processor.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": ""}
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            result = post_process(
                raw_transcript=raw_transcript,
                app_bundle_id=None,
                ledger=ledger,
                config=config,
            )

        assert result == raw_transcript


# ---------------------------------------------------------------------------
# Tests: Diff engine → ledger round-trip
# ---------------------------------------------------------------------------


class TestDiffEngineLedgerRoundTrip:
    """Verify that diff pairs extracted by the diff engine survive a full
    round-trip through ledger insert/query.
    """

    def test_diff_pairs_preserved_through_ledger(self, ledger, freeze_time):
        """Diff pairs stored in the ledger should match what the diff engine
        produced.
        """
        injected = "send the event to posthog"
        corrected = "send the event to PostHog"
        diff_pairs = extract_diff_pairs(injected, corrected)
        assert len(diff_pairs) >= 1

        # Insert 3 times to cross confidence threshold.
        for _ in range(3):
            ledger.insert_correction(
                injected_text=injected,
                corrected_text=corrected,
                diff_pairs=diff_pairs,
            )
            freeze_time(seconds=30)

        results = ledger.query_relevant_corrections(
            raw_transcript="send to posthog",
            min_confidence=0.5,
        )

        assert len(results) >= 1
        stored_pairs = [tuple(p) for p in results[0].diff_pairs]
        for pair in diff_pairs:
            assert pair in stored_pairs

    def test_app_context_boost_in_pipeline(self, ledger, freeze_time):
        """Corrections with matching app_bundle_id rank higher."""
        injected = "check the see lie output"
        corrected = "check the CLI output"
        app_id = "com.apple.Terminal"

        # Insert with app context, 3 times.
        for _ in range(3):
            _simulate_correction_cycle(
                ledger, injected, corrected, app_bundle_id=app_id,
            )
            freeze_time(seconds=30)

        # Insert same correction without app context, 3 times.
        injected2 = "update the to you I component"
        corrected2 = "update the TUI component"
        for _ in range(3):
            _simulate_correction_cycle(ledger, injected2, corrected2)
            freeze_time(seconds=30)

        # Query with matching app — CLI correction should rank higher.
        results = ledger.query_relevant_corrections(
            raw_transcript="check the see lie and the to you I",
            app_bundle_id=app_id,
            min_confidence=0.5,
        )

        assert len(results) >= 2
        # First result should be the one with app context boost.
        assert ("see lie", "CLI") in [tuple(p) for p in results[0].diff_pairs]

    def test_deduplication_across_cycles(self, ledger, freeze_time):
        """Repeated identical corrections should deduplicate, incrementing
        times_seen rather than creating new rows.
        """
        injected = "run docker compose up"
        corrected = "run docker-compose up"

        for _ in range(5):
            _simulate_correction_cycle(ledger, injected, corrected)
            freeze_time(seconds=60)

        all_corrections = ledger.list_corrections()
        # Should be exactly one correction with times_seen=5.
        matching = [
            c for c in all_corrections
            if ("docker compose", "docker-compose") in [tuple(p) for p in c.diff_pairs]
        ]
        assert len(matching) == 1
        assert matching[0].times_seen == 5
