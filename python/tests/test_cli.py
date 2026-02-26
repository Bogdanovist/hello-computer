"""Tests for CLI commands: status, test-ollama, corrections, config, pause/resume."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from vox.cli import main
from vox.ledger import Ledger

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


# ------------------------------------------------------------------
# Helper: create a ledger with sample data
# ------------------------------------------------------------------


def _make_ledger(db_path: Path) -> Ledger:
    """Create a ledger with two sample corrections and return it (open)."""
    ledger = Ledger(db_path, encryption_key=None)
    ledger.insert_correction("to you I", "TUI", [("to you I", "TUI")])
    ledger.insert_correction("see lie", "CLI", [("see lie", "CLI")],
                             app_bundle_id="com.microsoft.VSCode")
    return ledger


# ------------------------------------------------------------------
# vox corrections delete
# ------------------------------------------------------------------


def test_corrections_delete_valid_id(tmp_path):
    """Deletion of a valid ID removes it and shows confirmation."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "delete", "1"])

    assert result.exit_code == 0
    assert "Deleted correction #1." in result.output

    # Verify actually deleted from the DB.
    ledger = Ledger(db_path, encryption_key=None)
    row = ledger.connection.execute(
        "SELECT 1 FROM corrections WHERE id = 1",
    ).fetchone()
    ledger.close()
    assert row is None


def test_corrections_delete_invalid_id(tmp_path):
    """Deletion of a non-existent ID shows 'not found' message."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "delete", "999"])

    assert result.exit_code == 0
    assert "Correction #999 not found." in result.output


def test_corrections_delete_no_ledger(tmp_path):
    """Deletion when no ledger exists shows 'not found'."""
    runner = CliRunner()
    with patch("vox.cli._DB_PATH", tmp_path / "nonexistent.db"):
        result = runner.invoke(main, ["corrections", "delete", "1"])

    assert result.exit_code == 0
    assert "Correction #1 not found." in result.output


# ------------------------------------------------------------------
# vox corrections disable / enable
# ------------------------------------------------------------------


def test_corrections_disable_valid_id(tmp_path):
    """Disable sets active=0 and shows confirmation."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "disable", "1"])

    assert result.exit_code == 0
    assert "Disabled correction #1." in result.output

    # Verify active flag is 0.
    ledger = Ledger(db_path, encryption_key=None)
    active = ledger.connection.execute(
        "SELECT active FROM corrections WHERE id = 1",
    ).fetchone()[0]
    ledger.close()
    assert active == 0


def test_corrections_disable_invalid_id(tmp_path):
    """Disable of a non-existent ID shows 'not found'."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "disable", "999"])

    assert result.exit_code == 0
    assert "Correction #999 not found." in result.output


def test_corrections_enable_valid_id(tmp_path):
    """Enable re-activates a disabled correction with confirmation."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.disable_correction(1)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "enable", "1"])

    assert result.exit_code == 0
    assert "Enabled correction #1." in result.output

    # Verify active flag is 1 again.
    ledger = Ledger(db_path, encryption_key=None)
    active = ledger.connection.execute(
        "SELECT active FROM corrections WHERE id = 1",
    ).fetchone()[0]
    ledger.close()
    assert active == 1


def test_corrections_enable_invalid_id(tmp_path):
    """Enable of a non-existent ID shows 'not found'."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "enable", "999"])

    assert result.exit_code == 0
    assert "Correction #999 not found." in result.output


# ------------------------------------------------------------------
# vox corrections export
# ------------------------------------------------------------------


def test_corrections_export_format(tmp_path):
    """Export produces valid JSON with required fields."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "export"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2

    required_keys = {
        "created_at", "updated_at", "app_bundle_id", "raw_transcript",
        "injected_text", "corrected_text", "diff_pairs", "times_seen",
        "confidence", "active",
    }
    for entry in data:
        assert required_keys.issubset(entry.keys())
        assert isinstance(entry["diff_pairs"], list)
        assert isinstance(entry["times_seen"], int)
        assert isinstance(entry["active"], bool)


def test_corrections_export_empty_ledger(tmp_path):
    """Export with no corrections outputs an empty JSON array."""
    db_path = tmp_path / "corrections.db"
    # Create empty ledger.
    ledger = Ledger(db_path, encryption_key=None)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "export"])

    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_corrections_export_no_ledger(tmp_path):
    """Export when no ledger exists outputs an empty JSON array."""
    runner = CliRunner()
    with patch("vox.cli._DB_PATH", tmp_path / "nonexistent.db"):
        result = runner.invoke(main, ["corrections", "export"])

    assert result.exit_code == 0
    assert json.loads(result.output) == []


# ------------------------------------------------------------------
# vox corrections import
# ------------------------------------------------------------------


def test_corrections_import_new_entries(tmp_path):
    """Import adds new corrections to the ledger."""
    db_path = tmp_path / "corrections.db"
    import_data = [
        {
            "created_at": "2026-02-20 10:30:00",
            "updated_at": "2026-02-20 10:30:00",
            "app_bundle_id": None,
            "raw_transcript": "pie test",
            "injected_text": "pie test",
            "corrected_text": "pytest",
            "diff_pairs": [["pie test", "pytest"]],
            "times_seen": 3,
            "active": True,
        },
    ]
    import_file = tmp_path / "import.json"
    import_file.write_text(json.dumps(import_data), encoding="utf-8")

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "import", str(import_file)])

    assert result.exit_code == 0
    assert "Imported 1 correction(s)." in result.output

    # Verify the correction is in the DB.
    ledger = Ledger(db_path, encryption_key=None)
    records = ledger.list_corrections()
    ledger.close()
    assert len(records) == 1
    assert records[0].corrected_text == "pytest"


def test_corrections_import_merge_existing(tmp_path):
    """Import merges with existing entries by keeping higher times_seen."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    # Import with same diff_pairs but higher times_seen.
    import_data = [
        {
            "created_at": "2026-02-20 10:30:00",
            "updated_at": "2026-02-25 10:30:00",
            "app_bundle_id": None,
            "raw_transcript": "to you I",
            "injected_text": "to you I",
            "corrected_text": "TUI",
            "diff_pairs": [["to you I", "TUI"]],
            "times_seen": 10,
            "active": True,
        },
    ]
    import_file = tmp_path / "import.json"
    import_file.write_text(json.dumps(import_data), encoding="utf-8")

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "import", str(import_file)])

    assert result.exit_code == 0
    assert "Imported 1 correction(s)." in result.output

    # Verify times_seen was updated to the higher value.
    ledger = Ledger(db_path, encryption_key=None)
    row = ledger.connection.execute(
        "SELECT times_seen FROM corrections WHERE id = 1",
    ).fetchone()
    ledger.close()
    assert row[0] == 10


def test_corrections_import_invalid_json(tmp_path):
    """Import of malformed JSON shows a clear error."""
    import_file = tmp_path / "bad.json"
    import_file.write_text("not valid json {{{", encoding="utf-8")

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", tmp_path / "corrections.db"):
        result = runner.invoke(main, ["corrections", "import", str(import_file)])

    assert result.exit_code == 0
    assert "Invalid JSON" in result.output


# ------------------------------------------------------------------
# vox corrections reset
# ------------------------------------------------------------------


def test_corrections_reset_without_confirm(tmp_path):
    """Reset without --confirm does not delete and shows warning."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "reset"])

    assert result.exit_code == 0
    assert "Use --confirm to proceed" in result.output

    # Verify corrections still exist.
    ledger = Ledger(db_path, encryption_key=None)
    count = ledger.connection.execute(
        "SELECT COUNT(*) FROM corrections",
    ).fetchone()[0]
    ledger.close()
    assert count == 2


def test_corrections_reset_with_confirm(tmp_path):
    """Reset with --confirm creates backup and clears all corrections."""
    db_path = tmp_path / "corrections.db"
    ledger = _make_ledger(db_path)
    ledger.close()

    runner = CliRunner()
    with patch("vox.cli._DB_PATH", db_path):
        result = runner.invoke(main, ["corrections", "reset", "--confirm"])

    assert result.exit_code == 0
    assert "Backup created at" in result.output
    assert "All corrections have been deleted." in result.output

    # Verify corrections are gone.
    ledger = Ledger(db_path, encryption_key=None)
    count = ledger.connection.execute(
        "SELECT COUNT(*) FROM corrections",
    ).fetchone()[0]
    ledger.close()
    assert count == 0

    # Verify backup file was created.
    backups = list(tmp_path.glob("corrections_backup_*.json"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert len(backup_data) == 2


# ------------------------------------------------------------------
# vox config get
# ------------------------------------------------------------------


def test_config_get_valid_key(tmp_path):
    """config get prints the value for a known dotted key."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"

    runner = CliRunner()
    with (
        patch("vox.config.CONFIG_DIR", config_dir),
        patch("vox.config.CONFIG_FILE", config_file),
    ):
        result = runner.invoke(
            main, ["config", "get", "post_processing.ollama_model"],
        )

    assert result.exit_code == 0
    assert "qwen3:8b" in result.output


def test_config_get_unknown_key(tmp_path):
    """config get with unknown key shows error."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"

    runner = CliRunner()
    with (
        patch("vox.config.CONFIG_DIR", config_dir),
        patch("vox.config.CONFIG_FILE", config_file),
    ):
        result = runner.invoke(main, ["config", "get", "nonexistent.key"])

    assert result.exit_code != 0
    assert "Unknown config key" in result.output


# ------------------------------------------------------------------
# vox config set
# ------------------------------------------------------------------


def test_config_set_valid_value(tmp_path):
    """config set writes valid value and shows confirmation."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"

    runner = CliRunner()
    with (
        patch("vox.config.CONFIG_DIR", config_dir),
        patch("vox.config.CONFIG_FILE", config_file),
        patch("vox.cli._send_daemon_control", return_value=False),
    ):
        result = runner.invoke(
            main, ["config", "set", "post_processing.temperature", "0.5"],
        )

    assert result.exit_code == 0
    assert "Set post_processing.temperature." in result.output


def test_config_set_invalid_type(tmp_path):
    """config set with invalid type shows error."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"

    runner = CliRunner()
    with (
        patch("vox.config.CONFIG_DIR", config_dir),
        patch("vox.config.CONFIG_FILE", config_file),
        patch("vox.cli._send_daemon_control", return_value=False),
    ):
        result = runner.invoke(
            main, ["config", "set", "post_processing.temperature", "not_a_number"],
        )

    assert result.exit_code != 0
    assert "Invalid value for post_processing.temperature" in result.output


def test_config_set_unknown_key(tmp_path):
    """config set with unknown key shows error."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"

    runner = CliRunner()
    with (
        patch("vox.config.CONFIG_DIR", config_dir),
        patch("vox.config.CONFIG_FILE", config_file),
        patch("vox.cli._send_daemon_control", return_value=False),
    ):
        result = runner.invoke(
            main, ["config", "set", "nonexistent.key", "value"],
        )

    assert result.exit_code != 0
    assert "Unknown config key" in result.output


# ------------------------------------------------------------------
# vox pause / resume
# ------------------------------------------------------------------


def test_pause_observer_daemon_running():
    """pause sends observer pause to daemon and shows confirmation."""
    runner = CliRunner()
    with patch("vox.cli._send_daemon_control", return_value=True) as mock_ctrl:
        result = runner.invoke(main, ["pause"])

    assert result.exit_code == 0
    assert "Paused correction observer." in result.output
    mock_ctrl.assert_called_once_with(
        {"type": "control", "action": "pause", "scope": "observer"},
    )


def test_pause_full_daemon_running():
    """pause --full sends full pause to daemon and shows confirmation."""
    runner = CliRunner()
    with patch("vox.cli._send_daemon_control", return_value=True) as mock_ctrl:
        result = runner.invoke(main, ["pause", "--full"])

    assert result.exit_code == 0
    assert "Paused all Vox processing." in result.output
    mock_ctrl.assert_called_once_with(
        {"type": "control", "action": "pause", "scope": "full"},
    )


def test_pause_daemon_not_running():
    """pause shows error when daemon is not running."""
    runner = CliRunner()
    with patch("vox.cli._send_daemon_control", return_value=False):
        result = runner.invoke(main, ["pause"])

    assert result.exit_code == 0
    assert "Vox daemon is not running" in result.output


def test_resume_daemon_running():
    """resume sends resume message and shows confirmation."""
    runner = CliRunner()
    with patch("vox.cli._send_daemon_control", return_value=True) as mock_ctrl:
        result = runner.invoke(main, ["resume"])

    assert result.exit_code == 0
    assert "Resumed Vox processing." in result.output
    mock_ctrl.assert_called_once_with(
        {"type": "control", "action": "resume"},
    )


def test_resume_daemon_not_running():
    """resume shows error when daemon is not running."""
    runner = CliRunner()
    with patch("vox.cli._send_daemon_control", return_value=False):
        result = runner.invoke(main, ["resume"])

    assert result.exit_code == 0
    assert "Vox daemon is not running" in result.output
