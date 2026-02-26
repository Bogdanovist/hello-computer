"""Tests for the vox.config module."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest
import tomli_w

from vox.config import (
    CorrectionObserverConfig,
    VoxConfig,
    ensure_config_dir,
    load_config,
)


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Redirect all config paths to tmp_path so tests never touch ~/.vox/."""
    config_dir = tmp_path / ".vox"
    config_file = config_dir / "config.toml"
    monkeypatch.setattr("vox.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("vox.config.CONFIG_FILE", config_file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_toml(tmp_path: Path, data: dict) -> None:
    """Write a TOML config file into the isolated config dir."""
    config_dir = tmp_path / ".vox"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    with open(config_file, "wb") as f:
        tomli_w.dump(data, f)


# ---------------------------------------------------------------------------
# Load defaults (no file)
# ---------------------------------------------------------------------------

class TestLoadDefaults:
    def test_load_defaults_no_file(self, tmp_path):
        """Load config with no config file — all defaults applied."""
        config = VoxConfig.from_dict({})
        assert config.dictation.hotkey == "globe"
        assert config.dictation.whisper_model == "large-v3-turbo.en"
        assert config.dictation.language == "en"
        assert config.post_processing.enabled is True
        assert config.post_processing.ollama_port == 11434
        assert config.post_processing.temperature == 0.0
        assert config.correction_observer.correction_window_seconds == 30
        assert config.correction_observer.min_edit_ratio == 0.05
        assert config.correction_observer.max_edit_ratio == 0.80
        assert config.logging.level == "info"

    def test_load_config_no_file_returns_defaults(self, tmp_path):
        """load_config() with no config file returns all defaults."""
        config = load_config()
        assert config.dictation.hotkey == "globe"
        assert config.post_processing.ollama_model == "qwen3:8b"
        assert config.logging.level == "info"


# ---------------------------------------------------------------------------
# Load partial config
# ---------------------------------------------------------------------------

class TestLoadPartialConfig:
    def test_partial_config_uses_specified_keys(self, tmp_path):
        """Config file with some keys — specified keys used, defaults for rest."""
        _write_toml(tmp_path, {
            "dictation": {"hotkey": "fn"},
            "post_processing": {"temperature": 0.7},
        })
        config = load_config()
        assert config.dictation.hotkey == "fn"
        assert config.post_processing.temperature == 0.7
        # Unspecified fields use defaults
        assert config.dictation.whisper_model == "large-v3-turbo.en"
        assert config.post_processing.ollama_port == 11434
        assert config.correction_observer.debounce_seconds == 2.0
        assert config.logging.level == "info"


# ---------------------------------------------------------------------------
# Load full config
# ---------------------------------------------------------------------------

class TestLoadFullConfig:
    def test_full_config_all_values_from_file(self, tmp_path):
        """Config file with all keys — all values from file."""
        full = {
            "dictation": {
                "hotkey": "fn",
                "whisper_model": "base.en",
                "language": "de",
            },
            "post_processing": {
                "enabled": False,
                "ollama_model": "llama3:8b",
                "ollama_host": "localhost",
                "ollama_port": 8080,
                "ollama_keep_alive": "10m",
                "temperature": 0.5,
                "max_correction_pairs_in_prompt": 10,
                "confidence_threshold": 0.7,
                "hallucination_threshold": 0.3,
            },
            "correction_observer": {
                "enabled": False,
                "correction_window_seconds": 60,
                "debounce_seconds": 1.0,
                "min_edit_ratio": 0.10,
                "max_edit_ratio": 0.70,
                "auto_apply_after_n": 5,
            },
            "security": {
                "blocklist_bundle_ids": ["com.example.app"],
                "blocklist_title_patterns": ["secret"],
            },
            "logging": {
                "level": "debug",
                "log_file": "/tmp/vox-test.log",
            },
        }
        _write_toml(tmp_path, full)
        config = load_config()
        assert config.dictation.hotkey == "fn"
        assert config.dictation.whisper_model == "base.en"
        assert config.dictation.language == "de"
        assert config.post_processing.enabled is False
        assert config.post_processing.ollama_model == "llama3:8b"
        assert config.post_processing.ollama_port == 8080
        assert config.post_processing.temperature == 0.5
        assert config.correction_observer.enabled is False
        assert config.correction_observer.correction_window_seconds == 60
        assert config.correction_observer.min_edit_ratio == 0.10
        assert config.correction_observer.max_edit_ratio == 0.70
        assert config.security.blocklist_bundle_ids == ["com.example.app"]
        assert config.security.blocklist_title_patterns == ["secret"]
        assert config.logging.level == "debug"
        assert config.logging.log_file == "/tmp/vox-test.log"


# ---------------------------------------------------------------------------
# Invalid values fall back to defaults
# ---------------------------------------------------------------------------

class TestInvalidValuesFallback:
    def test_invalid_hotkey_uses_default(self, tmp_path, caplog):
        """Invalid (empty) hotkey value — default used, warning logged."""
        _write_toml(tmp_path, {"dictation": {"hotkey": ""}})
        with caplog.at_level(logging.WARNING):
            config = load_config()
        assert config.dictation.hotkey == "globe"
        assert "hotkey" in caplog.text.lower()

    def test_invalid_port_uses_default(self, tmp_path, caplog):
        """Port out of range — default used, warning logged."""
        _write_toml(tmp_path, {"post_processing": {"ollama_port": 99999}})
        with caplog.at_level(logging.WARNING):
            config = load_config()
        assert config.post_processing.ollama_port == 11434
        assert "ollama_port" in caplog.text

    def test_invalid_port_zero_uses_default(self, tmp_path, caplog):
        """Port of 0 — default used, warning logged."""
        _write_toml(tmp_path, {"post_processing": {"ollama_port": 0}})
        with caplog.at_level(logging.WARNING):
            config = load_config()
        assert config.post_processing.ollama_port == 11434

    def test_invalid_log_level_uses_default(self, tmp_path, caplog):
        """Invalid log level — default used, warning logged."""
        _write_toml(tmp_path, {"logging": {"level": "verbose"}})
        with caplog.at_level(logging.WARNING):
            config = load_config()
        assert config.logging.level == "info"
        assert "verbose" in caplog.text

    def test_min_edit_ratio_greater_than_max_uses_defaults(self, tmp_path, caplog):
        """min_edit_ratio > max_edit_ratio — both reset to defaults, warning logged."""
        _write_toml(tmp_path, {
            "correction_observer": {
                "min_edit_ratio": 0.9,
                "max_edit_ratio": 0.1,
            },
        })
        with caplog.at_level(logging.WARNING):
            config = load_config()
        defaults = CorrectionObserverConfig()
        assert config.correction_observer.min_edit_ratio == defaults.min_edit_ratio
        assert config.correction_observer.max_edit_ratio == defaults.max_edit_ratio
        assert "min_edit_ratio" in caplog.text

    def test_min_edit_ratio_equals_max_uses_defaults(self, tmp_path, caplog):
        """min_edit_ratio == max_edit_ratio — both reset to defaults."""
        _write_toml(tmp_path, {
            "correction_observer": {
                "min_edit_ratio": 0.5,
                "max_edit_ratio": 0.5,
            },
        })
        with caplog.at_level(logging.WARNING):
            config = load_config()
        defaults = CorrectionObserverConfig()
        assert config.correction_observer.min_edit_ratio == defaults.min_edit_ratio
        assert config.correction_observer.max_edit_ratio == defaults.max_edit_ratio


# ---------------------------------------------------------------------------
# Dotted key get/set
# ---------------------------------------------------------------------------

class TestDottedKeyAccess:
    def test_get_by_dotted_key(self):
        """Dotted key access works for all config sections."""
        config = VoxConfig.from_dict({})
        assert config.get_by_dotted_key("post_processing.temperature") == 0.0
        assert config.get_by_dotted_key("dictation.hotkey") == "globe"
        assert config.get_by_dotted_key("correction_observer.debounce_seconds") == 2.0
        assert config.get_by_dotted_key("logging.level") == "info"
        assert isinstance(
            config.get_by_dotted_key("security.blocklist_bundle_ids"), list,
        )

    def test_get_by_dotted_key_unknown_section_raises(self):
        """Unknown section raises KeyError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(KeyError, match="Unknown config section"):
            config.get_by_dotted_key("nonexistent.field")

    def test_get_by_dotted_key_unknown_field_raises(self):
        """Unknown field in known section raises KeyError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(KeyError, match="Unknown config key"):
            config.get_by_dotted_key("dictation.nonexistent")

    def test_get_by_dotted_key_no_dot_raises(self):
        """Key without a dot raises KeyError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(KeyError, match="Invalid dotted key"):
            config.get_by_dotted_key("nodot")

    def test_set_by_dotted_key_updates_value(self, tmp_path):
        """Setting a value updates the config and writes to file."""
        config = VoxConfig.from_dict({})
        config.set_by_dotted_key("post_processing.temperature", "0.5")
        assert config.post_processing.temperature == 0.5

    def test_set_by_dotted_key_writes_file(self, tmp_path):
        """set_by_dotted_key writes the updated config to disk."""
        (tmp_path / ".vox").mkdir(parents=True, exist_ok=True)
        config = VoxConfig.from_dict({})
        config.set_by_dotted_key("post_processing.temperature", "0.8")
        # Verify file was written
        config_file = tmp_path / ".vox" / "config.toml"
        assert config_file.exists()
        reloaded = load_config()
        assert reloaded.post_processing.temperature == 0.8

    def test_set_by_dotted_key_bool_conversion(self, tmp_path):
        """Setting a bool field via string works."""
        config = VoxConfig.from_dict({})
        config.set_by_dotted_key("post_processing.enabled", "false")
        assert config.post_processing.enabled is False

    def test_set_by_dotted_key_int_conversion(self, tmp_path):
        """Setting an int field via string works."""
        config = VoxConfig.from_dict({})
        config.set_by_dotted_key("post_processing.ollama_port", "8080")
        assert config.post_processing.ollama_port == 8080


# ---------------------------------------------------------------------------
# Set with invalid type raises ValueError
# ---------------------------------------------------------------------------

class TestSetInvalidType:
    def test_set_invalid_type_float_field(self):
        """Setting non-numeric string for a float field raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="Cannot convert"):
            config.set_by_dotted_key("post_processing.temperature", "hot")

    def test_set_invalid_type_int_field(self):
        """Setting non-numeric string for an int field raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="Cannot convert"):
            config.set_by_dotted_key("post_processing.ollama_port", "abc")

    def test_set_invalid_type_bool_field(self):
        """Setting invalid bool string raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="Cannot convert"):
            config.set_by_dotted_key("post_processing.enabled", "maybe")

    def test_set_invalid_port_range(self):
        """Setting port out of valid range raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="ollama_port"):
            config.set_by_dotted_key("post_processing.ollama_port", "99999")

    def test_set_invalid_log_level(self):
        """Setting invalid log level raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="log level"):
            config.set_by_dotted_key("logging.level", "verbose")

    def test_set_list_field_raises(self):
        """Setting a list field via dotted key raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="Cannot set list value"):
            config.set_by_dotted_key("security.blocklist_bundle_ids", "com.foo")

    def test_set_min_edit_ratio_above_max_raises(self):
        """Setting min_edit_ratio >= max_edit_ratio raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="min_edit_ratio"):
            config.set_by_dotted_key("correction_observer.min_edit_ratio", "0.9")

    def test_set_max_edit_ratio_below_min_raises(self):
        """Setting max_edit_ratio <= min_edit_ratio raises ValueError."""
        config = VoxConfig.from_dict({})
        with pytest.raises(ValueError, match="max_edit_ratio"):
            config.set_by_dotted_key("correction_observer.max_edit_ratio", "0.01")


# ---------------------------------------------------------------------------
# First-run directory creation and permissions
# ---------------------------------------------------------------------------

class TestFirstRunSetup:
    def test_first_run_creates_directory(self, tmp_path):
        """First run creates the config directory."""
        config_dir = tmp_path / ".vox"
        assert not config_dir.exists()
        ensure_config_dir()
        assert config_dir.exists()
        assert config_dir.is_dir()

    def test_first_run_directory_permissions(self, tmp_path):
        """Config directory created with 0700 permissions."""
        config_dir = tmp_path / ".vox"
        assert not config_dir.exists()
        ensure_config_dir()
        mode = config_dir.stat().st_mode
        assert stat.S_IMODE(mode) == 0o700

    def test_first_run_copies_default_config(self, tmp_path):
        """First run copies default config to the config directory."""
        config_dir = tmp_path / ".vox"
        config_file = config_dir / "config.toml"
        assert not config_file.exists()
        ensure_config_dir()
        assert config_file.exists()
        # Verify content is valid TOML with expected sections
        import tomllib
        with open(config_file, "rb") as f:
            data = tomllib.load(f)
        assert "dictation" in data
        assert "post_processing" in data

    def test_load_config_creates_dir_and_file(self, tmp_path):
        """load_config() on first run creates directory and config file."""
        config_dir = tmp_path / ".vox"
        assert not config_dir.exists()
        config = load_config()
        assert config_dir.exists()
        assert (config_dir / "config.toml").exists()
        assert config.dictation.hotkey == "globe"

    def test_ensure_config_dir_idempotent(self, tmp_path):
        """Calling ensure_config_dir twice does not error."""
        ensure_config_dir()
        ensure_config_dir()
        config_dir = tmp_path / ".vox"
        assert config_dir.exists()
