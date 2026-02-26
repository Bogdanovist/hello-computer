# Vox

A self-improving local voice-to-text agent for macOS. Vox replaces Apple Dictation with system-wide speech-to-text that learns from your corrections — every fix you make trains a personal model that improves future transcriptions. All processing runs locally on Apple Silicon with zero network egress.

## Features

- **On-device transcription** via whisper.cpp with Metal GPU acceleration
- **Learning loop** — observes when you correct transcription errors and stores them in an encrypted ledger
- **LLM post-processing** — applies accumulated corrections via a local Ollama model before injecting text
- **System-wide** — works in any text field via Accessibility API and CGEvent injection
- **Privacy-first** — no audio, transcripts, or corrections ever leave the machine
- **Enterprise-ready** — designed for MDM-managed environments with full security verification

## Prerequisites

- macOS 14 (Sonoma) or later
- Apple Silicon (M1/M2/M3/M4)
- 32 GB RAM (recommended for Qwen3 8B model)
- Xcode Command Line Tools (`xcode-select --install`)

## Installation

Run the install script from the repository root:

```bash
git clone https://github.com/Bogdanovist/hello-computer.git
cd hello-computer
scripts/install.sh
```

The script is idempotent (safe to run multiple times) and handles:

1. Homebrew and SQLCipher
2. uv (Python package manager)
3. Ollama with the Qwen3 8B model
4. whisper.cpp at a pinned commit with Metal GPU build
5. Swift components (release build)
6. Python environment setup
7. `~/.vox/` config directory (permissions `0700`)

After installation, complete setup:

1. Grant Accessibility API permission: **System Settings > Privacy & Security > Accessibility** — add VoxDaemon
2. Start the daemon (see Quick Start below)
3. Verify security: `scripts/verify_security.sh`

## Quick Start

Start the Vox daemon:

```bash
./swift/.build/release/VoxDaemon
```

Hold the **Globe key** and speak. Release the key to transcribe and inject text at the cursor.

Vox runs in the background — transcription happens on-device via whisper.cpp, then the local LLM applies any learned corrections before injecting the final text.

## CLI Reference

The `vox` CLI manages the daemon, correction ledger, and configuration.

### Status and Control

```bash
vox status                # Show daemon status, model info, correction stats
vox pause                 # Pause the correction observer
vox pause --full          # Pause everything (observer + post-processing)
vox resume                # Resume from paused state
vox test-ollama           # Verify Ollama endpoint, model, and latency
```

### Corrections

```bash
vox corrections list              # List active corrections with confidence scores
vox corrections list --app <ID>   # Filter by app bundle ID
vox corrections search <TERM>     # Search by original or corrected text
vox corrections delete <ID>       # Permanently delete a correction
vox corrections disable <ID>      # Exclude a correction from queries (preserved in DB)
vox corrections enable <ID>       # Re-enable a disabled correction
vox corrections export            # Export all corrections as JSON to stdout
vox corrections import <FILE>     # Import corrections from a JSON file
vox corrections reset --confirm   # Delete all corrections
```

### Configuration

```bash
vox config                # Open config file in $EDITOR
vox config get <KEY>      # Print a config value (dotted notation)
vox config set <KEY> <VALUE>  # Set a config value
```

## Configuration

Vox reads configuration from `~/.vox/config.toml`. Edit directly or use `vox config`.

### `[dictation]`

| Key | Default | Description |
|-----|---------|-------------|
| `hotkey` | `"globe"` | Activation key: `"globe"`, `"fn"`, or a custom key code |
| `whisper_model` | `"large-v3-turbo.en"` | Whisper model name |
| `language` | `"en"` | Language code for transcription |

### `[post_processing]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable LLM post-processing |
| `ollama_model` | `"qwen3:8b"` | Ollama model for post-processing |
| `ollama_host` | `"127.0.0.1"` | Ollama server host (localhost only) |
| `ollama_port` | `11434` | Ollama server port |
| `ollama_keep_alive` | `"5m"` | Duration to keep model loaded |
| `temperature` | `0` | LLM sampling temperature (0 = deterministic) |
| `max_correction_pairs_in_prompt` | `20` | Max correction pairs in LLM prompt |
| `confidence_threshold` | `0.5` | Minimum confidence for corrections in prompt |
| `hallucination_threshold` | `0.5` | Max edit distance ratio before discarding LLM output |

### `[correction_observer]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable automatic correction observation |
| `correction_window_seconds` | `30` | Seconds after injection to watch for corrections |
| `debounce_seconds` | `2` | Seconds of inactivity before capturing a correction |
| `min_edit_ratio` | `0.05` | Minimum edit distance ratio to count as a correction |
| `max_edit_ratio` | `0.80` | Maximum edit distance ratio to count as a correction |
| `auto_apply_after_n` | `3` | Times a correction must be seen before auto-applied |

### `[security]`

| Key | Default | Description |
|-----|---------|-------------|
| `blocklist_bundle_ids` | `[...]` | App bundle IDs where Vox never reads text fields |
| `blocklist_title_patterns` | `[...]` | Window title patterns that trigger the blocklist |

Default blocklist includes 1Password, Bitwarden, LastPass, Keychain Access, and System Settings. Title patterns block windows containing "password", "credential", "secret", "keychain", "ssh", or "gpg".

### `[logging]`

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `"info"` | Log level: `"debug"`, `"info"`, `"warn"`, `"error"` |
| `log_file` | `"~/.vox/vox.log"` | Log file path |

Transcription content is never written to logs — only metadata (timestamps, app IDs, latency, flags).

## Security

Vox is designed to pass formal security review in MDM-managed enterprise environments. Key properties:

- **Zero network egress** — all processing on-device (Ollama binds to `127.0.0.1` only)
- **Encrypted storage** — correction ledger uses SQLCipher with key stored in macOS Keychain
- **Content-free logging** — no transcribed text, corrections, or audio content in logs
- **App blocklist** — password managers and sensitive apps are excluded from observation
- **No keylogging** — only the activation hotkey is monitored
- **No clipboard, screen capture, or background audio access**

Verify security posture at any time:

```bash
scripts/verify_security.sh
```

This runs 9 checks: network egress, Ollama binding, ledger encryption, Keychain key, directory permissions, blocklist config, log content, audio temp files, and dependency hashes.

See [SECURITY.md](SECURITY.md) for the full threat model and MDM approval path.

## Troubleshooting

### Ollama not running

```
$ vox test-ollama
Error: Cannot connect to Ollama at 127.0.0.1:11434
```

Start Ollama and verify the model is available:

```bash
ollama serve &
ollama list    # Should show qwen3:8b
```

If the model is missing, pull it:

```bash
ollama pull qwen3:8b
```

### Accessibility API permission denied

Vox requires Accessibility API access to observe text field changes and inject text. If dictation works but corrections are not captured:

1. Open **System Settings > Privacy & Security > Accessibility**
2. Add VoxDaemon to the allowed list
3. Restart the daemon

In MDM-managed environments, IT must deploy a PPPC profile granting Accessibility access to VoxDaemon. See [SECURITY.md](SECURITY.md) for Jamf/Kandji configuration steps.

### Dictation produces no output

1. Check that the daemon is running: `vox status`
2. Verify whisper.cpp model exists: `ls vendor/whisper.cpp/models/ggml-large-v3-turbo.bin`
3. Check logs for errors: `tail -20 ~/.vox/vox.log`

### Post-processing not applying corrections

1. Verify post-processing is enabled: `vox config get post_processing.enabled`
2. Check Ollama connectivity: `vox test-ollama`
3. Verify corrections exist: `vox corrections list`
4. Check confidence threshold: corrections below `confidence_threshold` (default 0.5) are excluded from prompts

## Development

```bash
make build    # Build Swift (release) + sync Python environment
make test     # Run Swift and Python test suites
make clean    # Remove build artifacts
```

Lint Python code:

```bash
cd python && uv run ruff check .
```

## License

See [BILL_OF_MATERIALS.md](BILL_OF_MATERIALS.md) for all dependency licenses.
