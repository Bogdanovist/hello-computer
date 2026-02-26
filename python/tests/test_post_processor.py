"""Tests for the vox.post_processor module (T019).

Covers: prompt construction, prompt ordering, validation (normal, hallucination,
empty, None), full pipeline skip conditions, and Ollama connection error fallback.
All Ollama calls are mocked — no running Ollama instance required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from vox.config import PostProcessingConfig, VoxConfig
from vox.ledger import CorrectionRecord
from vox.post_processor import (
    call_ollama,
    construct_prompt,
    post_process,
    validate_output,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    diff_pairs: list[tuple[str, str]],
    confidence: float,
    *,
    record_id: int = 1,
    app_bundle_id: str | None = None,
) -> CorrectionRecord:
    """Create a minimal CorrectionRecord for testing."""
    now = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    return CorrectionRecord(
        id=record_id,
        created_at=now,
        updated_at=now,
        app_bundle_id=app_bundle_id,
        raw_transcript="raw",
        injected_text="injected",
        corrected_text="corrected",
        diff_pairs=diff_pairs,
        times_seen=3,
        confidence=confidence,
        active=True,
    )


def _default_pp_config(**overrides) -> PostProcessingConfig:
    """Return a PostProcessingConfig with optional overrides."""
    defaults = {
        "enabled": True,
        "ollama_model": "qwen3:8b",
        "ollama_host": "127.0.0.1",
        "ollama_port": 11434,
        "ollama_keep_alive": "5m",
        "temperature": 0.0,
        "max_correction_pairs_in_prompt": 20,
        "confidence_threshold": 0.5,
        "hallucination_threshold": 0.5,
    }
    defaults.update(overrides)
    return PostProcessingConfig(**defaults)


def _default_vox_config(**pp_overrides) -> VoxConfig:
    """Return a VoxConfig with PostProcessingConfig overrides."""
    config = VoxConfig()
    pp = _default_pp_config(**pp_overrides)
    config.post_processing = pp
    return config


# ---------------------------------------------------------------------------
# construct_prompt
# ---------------------------------------------------------------------------


class TestConstructPrompt:
    def test_prompt_format_with_corrections(self):
        """Prompt includes correction patterns and raw transcript."""
        corrections = [
            _make_record([("see lie", "CLI")], confidence=0.80),
        ]

        prompt = construct_prompt("fix the see lie tool", corrections)

        assert '"see lie" \u2192 "CLI"' in prompt
        assert "(confidence: 0.80)" in prompt
        assert "fix the see lie tool" in prompt
        assert "Known correction patterns" in prompt
        assert "Raw transcript" in prompt

    def test_prompt_ordering_by_confidence(self):
        """Corrections are ordered highest confidence first."""
        corrections = [
            _make_record([("grafana", "Grafana")], confidence=0.60, record_id=1),
            _make_record([("see lie", "CLI")], confidence=0.90, record_id=2),
            _make_record([("kubernets", "Kubernetes")], confidence=0.75, record_id=3),
        ]

        prompt = construct_prompt("some transcript", corrections)

        # Find positions of each correction in the prompt
        pos_cli = prompt.index('"see lie"')
        pos_kube = prompt.index('"kubernets"')
        pos_graf = prompt.index('"grafana"')

        assert pos_cli < pos_kube < pos_graf

    def test_prompt_multiple_diff_pairs_per_record(self):
        """Records with multiple diff_pairs expand into multiple lines."""
        corrections = [
            _make_record(
                [("see lie", "CLI"), ("grafana", "Grafana")],
                confidence=0.80,
            ),
        ]

        prompt = construct_prompt("transcript", corrections)

        assert '"see lie" \u2192 "CLI"' in prompt
        assert '"grafana" \u2192 "Grafana"' in prompt


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def test_normal_case_passes(self):
        """Valid LLM output with minor corrections passes validation."""
        config = _default_pp_config()
        result = validate_output(
            "fix the see lie tool",
            "fix the CLI tool",
            config,
        )
        assert result == "fix the CLI tool"

    def test_hallucination_detected(self):
        """LLM output >50% different from input is discarded."""
        config = _default_pp_config(hallucination_threshold=0.5)
        raw = "fix the see lie tool"
        # Completely different output — well over 50% edit distance ratio
        hallucinated = "The weather today is sunny and warm in Paris"

        result = validate_output(raw, hallucinated, config)
        assert result == raw

    def test_empty_output_fallback(self):
        """Empty string LLM output falls back to raw transcript."""
        config = _default_pp_config()
        raw = "fix the see lie tool"

        assert validate_output(raw, "", config) == raw
        assert validate_output(raw, "   ", config) == raw

    def test_none_output_fallback(self):
        """None LLM output (Ollama down) falls back to raw transcript."""
        config = _default_pp_config()
        raw = "fix the see lie tool"

        result = validate_output(raw, None, config)
        assert result == raw

    def test_identical_output_passes(self):
        """Identical output (no changes needed) passes validation."""
        config = _default_pp_config()
        raw = "this transcript is already correct"

        result = validate_output(raw, raw, config)
        assert result == raw


# ---------------------------------------------------------------------------
# call_ollama
# ---------------------------------------------------------------------------


class TestCallOllama:
    @patch("vox.post_processor.requests.post")
    def test_connection_error_returns_none(self, mock_post):
        """ConnectionError (Ollama not running) returns None."""
        import requests

        mock_post.side_effect = requests.ConnectionError("Connection refused")
        config = _default_pp_config()

        result = call_ollama("prompt", "raw transcript", config)
        assert result is None

    @patch("vox.post_processor.requests.post")
    def test_timeout_returns_none(self, mock_post):
        """Timeout returns None."""
        import requests

        mock_post.side_effect = requests.Timeout("Read timed out")
        config = _default_pp_config()

        result = call_ollama("prompt", "raw transcript", config)
        assert result is None

    @patch("vox.post_processor.requests.post")
    def test_successful_call_returns_response(self, mock_post):
        """Successful Ollama call returns the response text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "fix the CLI tool"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        config = _default_pp_config()
        result = call_ollama("prompt", "raw transcript", config)
        assert result == "fix the CLI tool"

    @patch("vox.post_processor.requests.post")
    def test_request_body_format(self, mock_post):
        """Verify the request body sent to Ollama matches spec."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "output"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        config = _default_pp_config(
            ollama_model="qwen3:8b",
            ollama_host="127.0.0.1",
            ollama_port=11434,
            temperature=0.0,
        )
        raw = "one two three"  # 3 words -> num_predict = 9
        call_ollama("the prompt", raw, config)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "http://127.0.0.1:11434/api/generate" in call_args.args or \
            call_args.kwargs.get("url", call_args.args[0] if call_args.args else "") == "http://127.0.0.1:11434/api/generate"

        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["model"] == "qwen3:8b"
        assert body["prompt"] == "the prompt"
        assert body["stream"] is False
        assert body["options"]["temperature"] == 0.0
        assert body["options"]["num_predict"] == 9


# ---------------------------------------------------------------------------
# post_process — skip conditions
# ---------------------------------------------------------------------------


class TestPostProcessSkipDisabled:
    def test_skip_when_disabled(self):
        """Post-processing disabled in config -> raw transcript returned."""
        config = _default_vox_config(enabled=False)
        ledger = MagicMock()

        result = post_process("raw transcript", None, ledger, config)

        assert result == "raw transcript"
        ledger.query_relevant_corrections.assert_not_called()


class TestPostProcessSkipEmptyLedger:
    @patch("vox.post_processor.call_ollama")
    def test_skip_on_empty_ledger(self, mock_ollama):
        """Empty ledger -> raw transcript returned, no Ollama call made."""
        config = _default_vox_config()
        ledger = MagicMock()
        ledger.query_relevant_corrections.return_value = []

        result = post_process("raw transcript", None, ledger, config)

        assert result == "raw transcript"
        mock_ollama.assert_not_called()


class TestPostProcessSkipNoRelevant:
    @patch("vox.post_processor.call_ollama")
    def test_skip_when_no_relevant_corrections(self, mock_ollama):
        """Corrections exist but none relevant -> raw transcript returned."""
        config = _default_vox_config()
        ledger = MagicMock()
        # query returns empty because none match the transcript
        ledger.query_relevant_corrections.return_value = []

        result = post_process("completely unrelated text", None, ledger, config)

        assert result == "completely unrelated text"
        mock_ollama.assert_not_called()


# ---------------------------------------------------------------------------
# post_process — Ollama error fallback
# ---------------------------------------------------------------------------


class TestPostProcessOllamaFallback:
    @patch("vox.post_processor.call_ollama")
    def test_ollama_connection_error_returns_raw(self, mock_ollama):
        """Ollama not running -> raw transcript returned, no crash."""
        mock_ollama.return_value = None

        config = _default_vox_config()
        ledger = MagicMock()
        ledger.query_relevant_corrections.return_value = [
            _make_record([("see lie", "CLI")], confidence=0.80),
        ]

        result = post_process("fix the see lie tool", None, ledger, config)

        assert result == "fix the see lie tool"


# ---------------------------------------------------------------------------
# post_process — full pipeline
# ---------------------------------------------------------------------------


class TestPostProcessFullPipeline:
    @patch("vox.post_processor.call_ollama")
    def test_end_to_end_with_mocked_ollama(self, mock_ollama):
        """Full pipeline: raw transcript -> corrected output via mocked Ollama."""
        mock_ollama.return_value = "fix the CLI tool"

        config = _default_vox_config()
        ledger = MagicMock()
        ledger.query_relevant_corrections.return_value = [
            _make_record([("see lie", "CLI")], confidence=0.80),
        ]

        result = post_process("fix the see lie tool", None, ledger, config)

        assert result == "fix the CLI tool"
        mock_ollama.assert_called_once()

    @patch("vox.post_processor.call_ollama")
    def test_hallucinated_output_falls_back_to_raw(self, mock_ollama):
        """Ollama returns hallucinated output -> raw transcript returned."""
        mock_ollama.return_value = "The weather today is sunny and warm in Paris"

        config = _default_vox_config()
        ledger = MagicMock()
        ledger.query_relevant_corrections.return_value = [
            _make_record([("see lie", "CLI")], confidence=0.80),
        ]

        result = post_process("fix the see lie tool", None, ledger, config)

        assert result == "fix the see lie tool"

    @patch("vox.post_processor.call_ollama")
    def test_query_receives_correct_parameters(self, mock_ollama):
        """Verify ledger.query_relevant_corrections receives config values."""
        mock_ollama.return_value = "output"

        config = _default_vox_config(
            max_correction_pairs_in_prompt=15,
            confidence_threshold=0.6,
        )
        ledger = MagicMock()
        ledger.query_relevant_corrections.return_value = [
            _make_record([("see lie", "CLI")], confidence=0.80),
        ]

        post_process("transcript", "com.app.test", ledger, config)

        ledger.query_relevant_corrections.assert_called_once_with(
            raw_transcript="transcript",
            app_bundle_id="com.app.test",
            limit=15,
            min_confidence=0.6,
        )
