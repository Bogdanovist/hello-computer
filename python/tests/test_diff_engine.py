"""Tests for the vox.diff_engine module."""

from __future__ import annotations

from vox.diff_engine import extract_diff_pairs

# ---------------------------------------------------------------------------
# Single word swap
# ---------------------------------------------------------------------------

class TestSingleWordSwap:
    def test_single_word_swap_see_lie_to_cli(self):
        """'see lie' → 'CLI' — phonetic misheard to acronym."""
        injected = "I need to update the see lie tool"
        corrected = "I need to update the CLI tool"
        pairs = extract_diff_pairs(injected, corrected)
        assert ("see lie", "CLI") in pairs

    def test_single_word_swap_grafana(self):
        """'graphana' → 'Grafana' — misspelling to product name."""
        injected = "open the graphana dashboard"
        corrected = "open the Grafana dashboard"
        pairs = extract_diff_pairs(injected, corrected)
        assert ("graphana", "Grafana") in pairs


# ---------------------------------------------------------------------------
# Multi-word to single word
# ---------------------------------------------------------------------------

class TestMultiToSingle:
    def test_multi_to_single_tui(self):
        """'to you I' → 'TUI' — multiple words to single acronym."""
        injected = "I need to update the to you I component"
        corrected = "I need to update the TUI component"
        pairs = extract_diff_pairs(injected, corrected)
        assert ("to you I", "TUI") in pairs


# ---------------------------------------------------------------------------
# Capitalisation
# ---------------------------------------------------------------------------

class TestCapitalisation:
    def test_capitalisation_posthog(self):
        """'posthog' → 'PostHog' — case change only."""
        injected = "send the event to posthog"
        corrected = "send the event to PostHog"
        pairs = extract_diff_pairs(injected, corrected)
        assert ("posthog", "PostHog") in pairs


# ---------------------------------------------------------------------------
# Hyphenation
# ---------------------------------------------------------------------------

class TestHyphenation:
    def test_hyphenation_docker_compose(self):
        """'docker compose' → 'docker-compose' — space to hyphen."""
        injected = "run docker compose up"
        corrected = "run docker-compose up"
        pairs = extract_diff_pairs(injected, corrected)
        assert ("docker compose", "docker-compose") in pairs


# ---------------------------------------------------------------------------
# Multiple corrections in one edit
# ---------------------------------------------------------------------------

class TestMultipleCorrections:
    def test_two_corrections_in_one_edit(self):
        """Two distinct substitutions extracted from a single edit."""
        injected = "update the to you I and the see lie"
        corrected = "update the TUI and the CLI"
        pairs = extract_diff_pairs(injected, corrected)
        assert len(pairs) == 2
        assert ("to you I", "TUI") in pairs
        assert ("see lie", "CLI") in pairs


# ---------------------------------------------------------------------------
# Edge cases: no differences
# ---------------------------------------------------------------------------

class TestNoDifferences:
    def test_identical_texts_return_empty(self):
        """Identical texts produce empty list."""
        pairs = extract_diff_pairs("hello world", "hello world")
        assert pairs == []

    def test_identical_long_text_return_empty(self):
        """Longer identical texts still produce empty list."""
        text = "the quick brown fox jumps over the lazy dog"
        pairs = extract_diff_pairs(text, text)
        assert pairs == []


# ---------------------------------------------------------------------------
# Edge cases: empty strings
# ---------------------------------------------------------------------------

class TestEmptyStrings:
    def test_both_empty(self):
        """Both empty strings produce empty list."""
        assert extract_diff_pairs("", "") == []

    def test_injected_empty(self):
        """Empty injected with non-empty corrected produces empty list."""
        assert extract_diff_pairs("", "hello") == []

    def test_corrected_empty(self):
        """Non-empty injected with empty corrected produces empty list."""
        assert extract_diff_pairs("hello", "") == []


# ---------------------------------------------------------------------------
# Edge cases: whitespace-only changes
# ---------------------------------------------------------------------------

class TestWhitespaceOnly:
    def test_whitespace_only_change_filtered(self):
        """Pure whitespace changes produce empty list."""
        pairs = extract_diff_pairs("hello  world", "hello world")
        assert pairs == []
