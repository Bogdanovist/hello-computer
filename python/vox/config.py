"""Vox configuration — dataclasses, loading, validation, and first-run setup."""

from __future__ import annotations

import logging
import shutil
import tomllib
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

import tomli_w

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".vox"
CONFIG_FILE = CONFIG_DIR / "config.toml"
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG = _PACKAGE_ROOT / "config" / "default.toml"

_VALID_LOG_LEVELS = {"debug", "info", "warn", "error"}


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


# Section name → dataclass class, populated after VoxConfig definition.
_SECTION_CLASSES: dict[str, type] = {}


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
        config = cls(
            dictation=_merge_section(
                "dictation", DictationConfig, sect("dictation", {}),
            ),
            post_processing=_merge_section(
                "post_processing", PostProcessingConfig,
                sect("post_processing", {}),
            ),
            correction_observer=_merge_section(
                "correction_observer", CorrectionObserverConfig,
                sect("correction_observer", {}),
            ),
            security=_merge_section(
                "security", SecurityConfig, sect("security", {}),
            ),
            logging=_merge_section(
                "logging", LoggingConfig, sect("logging", {}),
            ),
        )
        _validate_cross_fields(config)
        return config

    def get_by_dotted_key(self, key: str) -> Any:
        """Get a config value by dotted key path.

        Example: get_by_dotted_key('post_processing.temperature')
        """
        section_name, field_name = _parse_dotted_key(key)
        section = getattr(self, section_name)
        return getattr(section, field_name)

    def set_by_dotted_key(self, key: str, value: str) -> None:
        """Set a config value by dotted key path. Validates type. Raises ValueError."""
        section_name, field_name = _parse_dotted_key(key)
        section_cls = _SECTION_CLASSES[section_name]

        # Determine expected type from defaults
        defaults = section_cls()
        default_value = getattr(defaults, field_name)
        expected_type = type(default_value)

        # Convert string value to expected type
        converted = _convert_value(value, expected_type, field_name)

        # Validate the individual field
        error = _validate_field(section_name, field_name, converted)
        if error:
            raise ValueError(error)

        # Cross-field validation for edit ratios
        section = getattr(self, section_name)
        if section_name == "correction_observer":
            if (field_name == "min_edit_ratio"
                    and converted >= section.max_edit_ratio):
                raise ValueError(
                    f"min_edit_ratio ({converted}) must be less than "
                    f"max_edit_ratio ({section.max_edit_ratio})",
                )
            if (field_name == "max_edit_ratio"
                    and section.min_edit_ratio >= converted):
                raise ValueError(
                    f"min_edit_ratio ({section.min_edit_ratio}) must be less "
                    f"than max_edit_ratio ({converted})",
                )

        # Update in memory
        setattr(section, field_name, converted)

        # Write to file
        _write_config_file(self)


_SECTION_CLASSES.update({
    "dictation": DictationConfig,
    "post_processing": PostProcessingConfig,
    "correction_observer": CorrectionObserverConfig,
    "security": SecurityConfig,
    "logging": LoggingConfig,
})


def _parse_dotted_key(key: str) -> tuple[str, str]:
    """Parse 'section.field' into (section_name, field_name). Raises KeyError."""
    parts = key.split(".", 1)
    if len(parts) != 2:
        raise KeyError(f"Invalid dotted key: {key!r} — expected 'section.field'")
    section_name, field_name = parts
    if section_name not in _SECTION_CLASSES:
        raise KeyError(f"Unknown config section: {section_name!r}")
    section_cls = _SECTION_CLASSES[section_name]
    known = {f.name for f in dataclass_fields(section_cls)}
    if field_name not in known:
        raise KeyError(
            f"Unknown config key: {field_name!r} in section {section_name!r}",
        )
    return section_name, field_name


def _convert_value(value: str, expected_type: type, field_name: str) -> Any:
    """Convert a string CLI value to the expected Python type."""
    if expected_type is str:
        return value
    if expected_type is bool:
        low = value.lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ValueError(f"Cannot convert {value!r} to bool for {field_name}")
    if expected_type is int:
        try:
            return int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Cannot convert {value!r} to int for {field_name}",
            ) from exc
    if expected_type is float:
        try:
            return float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Cannot convert {value!r} to float for {field_name}",
            ) from exc
    if expected_type is list:
        raise ValueError(
            f"Cannot set list value via dotted key for {field_name}",
        )
    raise ValueError(f"Unsupported type {expected_type} for {field_name}")


def _validate_field(section_name: str, field_name: str, value: Any) -> str | None:
    """Return error message if value is invalid for this field, else None."""
    if section_name == "dictation" and field_name == "hotkey":
        if not isinstance(value, str) or not value:
            return f"hotkey must be a non-empty string, got {value!r}"
    if section_name == "post_processing" and field_name == "ollama_port":
        if isinstance(value, bool) or not isinstance(value, int) or not (
            1 <= value <= 65535
        ):
            return f"ollama_port must be an integer in range 1-65535, got {value!r}"
    if section_name == "logging" and field_name == "level":
        if value not in _VALID_LOG_LEVELS:
            return (
                f"log level must be one of {sorted(_VALID_LOG_LEVELS)}, "
                f"got {value!r}"
            )
    return None


def _validate_cross_fields(config: VoxConfig) -> None:
    """Cross-field validation. Resets to defaults with warning if invalid."""
    co = config.correction_observer
    if co.min_edit_ratio >= co.max_edit_ratio:
        defaults = CorrectionObserverConfig()
        logger.warning(
            "min_edit_ratio (%s) >= max_edit_ratio (%s); using defaults",
            co.min_edit_ratio,
            co.max_edit_ratio,
        )
        co.min_edit_ratio = defaults.min_edit_ratio
        co.max_edit_ratio = defaults.max_edit_ratio


def _merge_section(section_name: str, cls: type, section: dict):
    """Instantiate a config dataclass, validating values and using defaults."""
    defaults = cls()
    known_fields = {f.name for f in dataclass_fields(cls)}
    kwargs = {}
    for key, value in section.items():
        if key not in known_fields:
            logger.debug("Ignoring unknown config key: %s.%s", section_name, key)
            continue
        error = _validate_field(section_name, key, value)
        if error:
            logger.warning("%s — using default", error)
            continue
        kwargs[key] = value
    for fname in known_fields:
        if fname not in kwargs:
            kwargs[fname] = getattr(defaults, fname)
    return cls(**kwargs)


def _write_config_file(config: VoxConfig) -> None:
    """Write the current config to ~/.vox/config.toml using tomli_w."""
    ensure_config_dir()
    data = asdict(config)
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)


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
