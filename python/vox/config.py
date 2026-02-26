"""Vox configuration — dataclasses, loading, and first-run setup."""

from __future__ import annotations

import logging
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".vox"
CONFIG_FILE = CONFIG_DIR / "config.toml"
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG = _PACKAGE_ROOT / "config" / "default.toml"


@dataclass
class DictationConfig:
    hotkey: str = "globe"
    whisper_model: str = "large-v3-turbo.en"
    language: str = "en"


@dataclass
class PostProcessingConfig:
    enabled: bool = True
    ollama_model: str = "qwen3:8b"
    ollama_host: str = "127.0.0.1"
    ollama_port: int = 11434
    ollama_keep_alive: str = "5m"
    temperature: float = 0.0
    max_correction_pairs_in_prompt: int = 20
    confidence_threshold: float = 0.5
    hallucination_threshold: float = 0.5


@dataclass
class CorrectionObserverConfig:
    enabled: bool = True
    correction_window_seconds: int = 30
    debounce_seconds: float = 2.0
    min_edit_ratio: float = 0.05
    max_edit_ratio: float = 0.80
    auto_apply_after_n: int = 3


@dataclass
class SecurityConfig:
    blocklist_bundle_ids: list[str] = field(default_factory=lambda: [
        "com.1password.1password",
        "com.agilebits.onepassword7",
        "com.apple.keychainaccess",
        "com.apple.systempreferences",
        "com.bitwarden.desktop",
        "com.lastpass.LastPass",
    ])
    blocklist_title_patterns: list[str] = field(default_factory=lambda: [
        "password", "credential", "secret", "keychain", "ssh", "gpg",
    ])


@dataclass
class LoggingConfig:
    level: str = "info"
    log_file: str = "~/.vox/vox.log"


@dataclass
class VoxConfig:
    dictation: DictationConfig = field(default_factory=DictationConfig)
    post_processing: PostProcessingConfig = field(default_factory=PostProcessingConfig)
    correction_observer: CorrectionObserverConfig = field(
        default_factory=CorrectionObserverConfig,
    )
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_dict(cls, raw: dict) -> VoxConfig:
        """Create config from parsed TOML, defaults for missing."""
        sect = raw.get
        return cls(
            dictation=_merge_section(
                DictationConfig, sect("dictation", {}),
            ),
            post_processing=_merge_section(
                PostProcessingConfig, sect("post_processing", {}),
            ),
            correction_observer=_merge_section(
                CorrectionObserverConfig,
                sect("correction_observer", {}),
            ),
            security=_merge_section(
                SecurityConfig, sect("security", {}),
            ),
            logging=_merge_section(
                LoggingConfig, sect("logging", {}),
            ),
        )


def _merge_section(cls: type, section: dict):
    """Instantiate a config dataclass, applying only keys that match known fields."""
    defaults = cls()
    known_fields = {f.name for f in cls.__dataclass_fields__.values()}
    kwargs = {}
    for key, value in section.items():
        if key not in known_fields:
            logger.debug("Ignoring unknown config key: %s", key)
            continue
        kwargs[key] = value
    # Merge: start from defaults, override with provided values.
    for fname in known_fields:
        if fname not in kwargs:
            kwargs[fname] = getattr(defaults, fname)
    return cls(**kwargs)


def ensure_config_dir() -> None:
    """Create ~/.vox/ with 0700 permissions and copy default config on first run."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(mode=0o700, parents=True)
        logger.info("Created config directory: %s", CONFIG_DIR)

    if not CONFIG_FILE.exists() and _DEFAULT_CONFIG.exists():
        shutil.copy2(_DEFAULT_CONFIG, CONFIG_FILE)
        logger.info("Copied default config to %s", CONFIG_FILE)


def load_config() -> VoxConfig:
    """Load config from ~/.vox/config.toml, applying defaults for missing keys."""
    ensure_config_dir()

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            raw = tomllib.load(f)
    else:
        raw = {}

    return VoxConfig.from_dict(raw)
