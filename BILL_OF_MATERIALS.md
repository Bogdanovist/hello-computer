# Bill of Materials

All external dependencies for Vox, with pinned versions and provenance.
Verify hashes after installation with `scripts/verify_security.sh`.

## Runtime Dependencies

| Dependency | Version | Commit / Digest | License | Source | SHA256 |
|---|---|---|---|---|---|
| whisper.cpp | v1.7.3 | `f68298ce06ca0c50d09e3e0e07fc60b99e4fac55` | MIT | https://github.com/ggerganov/whisper.cpp | Verify: `git -C vendor/whisper.cpp rev-parse HEAD` |
| ggml-large-v3-turbo.bin | — | — | MIT | https://huggingface.co/ggerganov/whisper.cpp | Verify: `shasum -a 256 vendor/whisper.cpp/models/ggml-large-v3-turbo.bin` |
| Ollama | latest | — | MIT | https://github.com/ollama/ollama | Verify: `ollama --version` |
| Qwen3 8B (Q4_K_M) | qwen3:8b | — | Apache 2.0 | https://ollama.com/library/qwen3 | Verify: `ollama show qwen3:8b --modelfile` |
| SQLCipher | Homebrew latest | — | BSD-3-Clause | https://github.com/sqlcipher/sqlcipher | Verify: `brew info sqlcipher` |
| open-wispr (fork) | main | Pinned in `scripts/install.sh` | MIT | https://github.com/Bogdanovist/open-wispr | Verify: `git -C vendor/open-wispr rev-parse HEAD` |

## Build Dependencies

| Dependency | Version | License | Source |
|---|---|---|---|
| Swift | 5.9+ | Apache 2.0 | Apple (Xcode Command Line Tools) |
| Python | 3.12+ | PSF | https://www.python.org (via uv) |
| uv | latest | MIT / Apache 2.0 | https://astral.sh/uv |
| Xcode Command Line Tools | macOS 14+ | Apple EULA | Apple |
| Homebrew | latest | BSD-2-Clause | https://brew.sh |
| hatchling | latest | MIT | https://pypi.org/project/hatchling/ |

## Python Dependencies

Direct dependencies (from `pyproject.toml`, pinned in `python/uv.lock`):

| Package | Version | License | Purpose |
|---|---|---|---|
| requests | 2.32.5 | Apache 2.0 | Ollama API calls (localhost only) |
| click | 8.3.1 | BSD-3-Clause | CLI framework |
| tomli-w | 1.2.0 | MIT | TOML writing for config updates |
| pysqlcipher3 | 1.2.0 | zlib/libpng | SQLCipher Python bindings (optional, macOS only) |

Transitive dependencies (pulled in by direct deps):

| Package | Version | License | Pulled by |
|---|---|---|---|
| certifi | 2026.2.25 | MPL-2.0 | requests |
| charset-normalizer | 3.4.4 | MIT | requests |
| idna | 3.11 | BSD-3-Clause | requests |
| urllib3 | 2.6.3 | MIT | requests |

## Stdlib Dependencies (no external risk)

These are Python standard library modules — no supply-chain risk, no external downloads.

- `difflib` — diff computation for correction matching
- `tomllib` — TOML reading (Python 3.11+)
- `json` — JSON serialization for IPC and ledger
- `pathlib` — file path handling
- `dataclasses` — structured config and ledger records
- `sqlite3` — database driver (used with SQLCipher on macOS, plain SQLite in tests)
- `logging` — structured logging (content-free)
- `socket` — Unix domain socket IPC
- `datetime` — timestamp handling in ledger
- `os` — environment and process utilities
- `subprocess` — spawning external processes
- `shutil` — file copy for config setup
- `time` — timing utilities in CLI
