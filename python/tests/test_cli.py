"""Tests for CLI status and test-ollama commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from vox.cli import main

# ------------------------------------------------------------------
# vox status — daemon not running
# ------------------------------------------------------------------


def test_status_daemon_not_running(tmp_path):
    """status shows 'not running' when daemon socket is unreachable."""
    runner = CliRunner()
    with (
        patch("vox.cli._query_daemon_status", return_value=None),
        patch("vox.cli._DB_PATH", tmp_path / "corrections.db"),
        patch("vox.cli.load_config"),
    ):
        result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "Daemon:       not running" in result.output
    assert "0 active, 0 disabled (from ledger)" in result.output


def test_status_daemon_running(tmp_path):
    """status shows daemon info when daemon responds."""
    daemon_resp = {
        "pid": 12345,
        "uptime": "2h 34m",
        "whisper_model": "large-v3-turbo.en",
        "ollama_status": "warm",
        "last_dictation": "3m ago (VS Code)",
    }
    runner = CliRunner()
    with (
        patch("vox.cli._query_daemon_status", return_value=daemon_resp),
        patch("vox.cli._DB_PATH", tmp_path / "corrections.db"),
        patch("vox.cli.load_config") as mock_cfg,
    ):
        cfg = mock_cfg.return_value
        cfg.dictation.whisper_model = "large-v3-turbo.en"
        cfg.post_processing.ollama_model = "qwen3:8b"
        cfg.post_processing.ollama_host = "127.0.0.1"
        cfg.post_processing.ollama_port = 11434
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "running (pid 12345)" in result.output
    assert "Uptime:       2h 34m" in result.output
    assert "Whisper:      large-v3-turbo.en (loaded)" in result.output
    assert "qwen3:8b" in result.output
    assert "warm" in result.output
    assert "Last dictation: 3m ago (VS Code)" in result.output


def test_status_correction_counts(tmp_path):
    """status shows correct active/disabled counts from the ledger."""
    from vox.ledger import Ledger

    db_path = tmp_path / "corrections.db"
    ledger = Ledger(db_path, encryption_key=None)
    # Insert 2 active corrections.
    ledger.insert_correction("hello", "Hello", [("hello", "Hello")])
    ledger.insert_correction("wrold", "world", [("wrold", "world")])
    # Disable one.
    ledger.disable_correction(1)
    ledger.close()

    runner = CliRunner()
    with (
        patch("vox.cli._query_daemon_status", return_value=None),
        patch("vox.cli._DB_PATH", db_path),
        patch("vox.cli.load_config"),
    ):
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "1 active, 1 disabled" in result.output


def test_status_no_ledger_file(tmp_path):
    """status works gracefully when the ledger DB does not exist."""
    runner = CliRunner()
    with (
        patch("vox.cli._query_daemon_status", return_value=None),
        patch("vox.cli._DB_PATH", tmp_path / "nonexistent.db"),
        patch("vox.cli.load_config"),
    ):
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "0 active, 0 disabled" in result.output


# ------------------------------------------------------------------
# vox test-ollama — happy path
# ------------------------------------------------------------------


def _mock_responses(host, port, model):
    """Build a side_effect function for requests.{get,post}."""

    def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/api/tags"):
            resp.json.return_value = {
                "models": [{"name": f"{model}:latest"}],
            }
        return resp

    def _post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"response": "Hello"}
        return resp

    return _get, _post


def test_test_ollama_happy_path():
    """test-ollama shows endpoint, model, and test results on success."""
    runner = CliRunner()
    get_fn, post_fn = _mock_responses("127.0.0.1", 11434, "qwen3:8b")
    with (
        patch("vox.cli.load_config") as mock_cfg,
        patch("vox.cli.requests") as mock_requests,
    ):
        pp = mock_cfg.return_value.post_processing
        pp.ollama_host = "127.0.0.1"
        pp.ollama_port = 11434
        pp.ollama_model = "qwen3:8b"
        mock_requests.get.side_effect = get_fn
        mock_requests.post.side_effect = post_fn
        mock_requests.ConnectionError = ConnectionError
        mock_requests.Timeout = TimeoutError
        mock_requests.RequestException = Exception
        result = runner.invoke(main, ["test-ollama"])

    assert result.exit_code == 0
    assert "127.0.0.1:11434 ✓" in result.output
    assert "qwen3:8b ✓ (loaded)" in result.output
    assert '"Hello"' in result.output
    assert "ms)" in result.output


# ------------------------------------------------------------------
# vox test-ollama — daemon not running / Ollama not reachable
# ------------------------------------------------------------------


def test_test_ollama_endpoint_unreachable():
    """test-ollama shows error when Ollama endpoint is unreachable."""
    import requests as real_requests

    runner = CliRunner()
    with (
        patch("vox.cli.load_config") as mock_cfg,
        patch("vox.cli.requests") as mock_requests,
    ):
        pp = mock_cfg.return_value.post_processing
        pp.ollama_host = "127.0.0.1"
        pp.ollama_port = 11434
        pp.ollama_model = "qwen3:8b"
        mock_requests.get.side_effect = real_requests.ConnectionError("refused")
        mock_requests.ConnectionError = real_requests.ConnectionError
        mock_requests.Timeout = real_requests.Timeout
        mock_requests.RequestException = real_requests.RequestException
        result = runner.invoke(main, ["test-ollama"])

    assert result.exit_code == 0
    assert "✗ (not reachable)" in result.output


def test_test_ollama_model_not_found():
    """test-ollama shows error when configured model is not available."""
    runner = CliRunner()

    def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/api/tags"):
            resp.json.return_value = {"models": [{"name": "other:latest"}]}
        return resp

    with (
        patch("vox.cli.load_config") as mock_cfg,
        patch("vox.cli.requests") as mock_requests,
    ):
        pp = mock_cfg.return_value.post_processing
        pp.ollama_host = "127.0.0.1"
        pp.ollama_port = 11434
        pp.ollama_model = "qwen3:8b"
        mock_requests.get.side_effect = _get
        mock_requests.ConnectionError = ConnectionError
        mock_requests.Timeout = TimeoutError
        mock_requests.RequestException = Exception
        result = runner.invoke(main, ["test-ollama"])

    assert result.exit_code == 0
    assert "qwen3:8b ✗ (not found)" in result.output
