# Vox Security Model

## Executive Summary

Vox is a local-only voice-to-text agent for macOS that captures audio via the system microphone, transcribes it on-device using whisper.cpp (Metal GPU), optionally post-processes the transcript with a local LLM (Ollama on localhost:11434), and injects the resulting text at the active cursor via CGEvent keystrokes. Vox observes user corrections to transcription errors through the macOS Accessibility API and stores them in an encrypted SQLCipher database. **All processing runs on-device — zero network egress.** No audio, transcription text, or correction data ever leaves the machine. The system is designed to pass formal security review in MDM-managed enterprise environments.

## Privilege Inventory

Every macOS permission Vox requires, with justification:

| Permission | API / Entitlement | Justification |
|---|---|---|
| Microphone | `AVCaptureDevice` (audio) | Records speech while the user holds the dictation hotkey. Microphone is active only during the hotkey hold — no wake word, no background listening. |
| Accessibility | `AXUIElement` API | **Read**: Observes the focused text field value after text injection to detect user corrections. **Write**: Injects transcribed text at the active cursor via `CGEvent` keystroke synthesis. |
| Input Monitoring | `CGEvent` tap | Detects the dictation hotkey (Globe/Fn key press and release). Does not log or record any other keystrokes. |

No other macOS permissions are requested. Vox does not use Location Services, Contacts, Calendar, Photos, Camera, Screen Recording, Full Disk Access, or any network entitlements.

## What Vox Does NOT Have Access To

This section explicitly enumerates capabilities Vox does **not** possess:

- **No network access** — Vox makes zero outbound network connections. The only network activity is Ollama communication on `127.0.0.1:11434` (loopback). Verifiable: `lsof -i -P | grep VoxDaemon` shows no results (or only localhost).
- **No screen capture** — Vox does not use the Screen Recording API. It reads only the focused text field value, not screen contents.
- **No keylogging** — The `CGEvent` tap monitors only the configured hotkey (Globe/Fn). No other keystrokes are captured, logged, or stored.
- **No clipboard access** — Vox does not read or write the system clipboard (`NSPasteboard`).
- **No background audio recording** — The microphone activates only while the hotkey is physically held down. Release of the hotkey immediately stops recording and deletes the temporary audio file.
- **No cloud/telemetry** — No update checks, no crash reporting, no analytics, no data transmission of any kind.
- **No access to blocklisted apps** — The Accessibility observer is completely disabled for apps in the configurable blocklist (1Password, Keychain Access, etc.). No `AXValue` reads occur for these apps.

## Data Flow

```
                        ┌─────────────────────────────────┐
                        │       User holds hotkey          │
                        └───────────────┬─────────────────┘
                                        │
                                        ▼
                        ┌─────────────────────────────────┐
                        │   Microphone capture (temp file) │
                        │   Deleted immediately after use  │
                        └───────────────┬─────────────────┘
                                        │ audio
                                        ▼
                        ┌─────────────────────────────────┐
                        │   whisper.cpp (Metal GPU)        │
                        │   In-process, no network         │
                        └───────────────┬─────────────────┘
                                        │ raw transcript
                                        ▼
                        ┌─────────────────────────────────┐
                        │   Post-Processor                 │
                        │   Ollama @ 127.0.0.1:11434       │◄── Correction Ledger
                        │   Loopback only, never 0.0.0.0   │    (encrypted, local)
                        └───────────────┬─────────────────┘
                                        │ cleaned transcript
                                        ▼
                        ┌─────────────────────────────────┐
                        │   CGEvent text injection         │
                        │   Keystrokes at active cursor    │
                        └───────────────┬─────────────────┘
                                        │ injected text + timestamp
                                        ▼
                        ┌─────────────────────────────────┐
                        │   Correction Observer            │
                        │   AXValueChanged / polling       │
                        │   Blocklist enforced             │
                        └───────────────┬─────────────────┘
                                        │ correction pair (if edit detected)
                                        ▼
                        ┌─────────────────────────────────┐
                        │   Correction Ledger              │
                        │   SQLCipher AES-256-CBC          │
                        │   Key in macOS Keychain          │
                        └─────────────────────────────────┘

Storage:
  ~/.vox/corrections.db ─── SQLCipher encrypted (AES-256-CBC)
  ~/.vox/config.toml ────── Plaintext (no secrets, dir is 0700)
  ~/.vox/vox.log ────────── Metadata only (content-free logging)
  macOS Keychain ────────── com.vox.ledger encryption key
```

## Threat Matrix

| ID | Threat | Severity | Mitigations |
|---|---|---|---|
| T1 | **Network data exfiltration** — audio, transcripts, or correction data transmitted off-device | Critical | Vox makes zero network connections. whisper.cpp runs in-process. Ollama binds to `127.0.0.1` only. Verified at runtime by `verify_security.sh` (checks 1–2). No cloud APIs, no telemetry, no update checks. |
| T2 | **Accessibility API over-read** — reading sensitive fields in password managers or credential dialogs | High | App blocklist (`security.blocklist_bundle_ids`) disables all AX observation for sensitive apps (1Password, Keychain Access, Bitwarden, LastPass, System Settings). Window title pattern matching (`blocklist_title_patterns`) catches "password", "credential", "secret", "keychain", "ssh", "gpg". Observer is fully disabled — no `AXValue` reads occur. |
| T3 | **Correction data exposure at rest** — unencrypted ledger readable by other processes or users | High | Correction ledger uses SQLCipher (AES-256-CBC). Encryption key stored in macOS Keychain (`com.vox.ledger`), backed by Secure Enclave where available. `~/.vox/` directory permissions are `0700`. Plain `sqlite3` cannot read the database. Verified by `verify_security.sh` (checks 3–5). |
| T4 | **Content leakage via logs** — transcribed or corrected text appearing in log files | Medium | Content-free logging policy: logs contain only metadata (timestamps, app bundle IDs, latency in ms, boolean flags). Transcribed text, corrected text, correction pairs, AX values, Ollama prompts, and Ollama responses are never logged. Verified by `verify_security.sh` (check 7). |
| T5 | **Audio data persistence** — temporary audio files remaining on disk after processing | Medium | Audio is captured to a temporary file (`/tmp/vox_audio_*`) and deleted immediately after whisper.cpp transcription completes. No audio is retained. Verified by `verify_security.sh` (check 8). |
| T6 | **Supply chain compromise** — malicious code in a dependency | Medium | All dependencies pinned to specific versions or commit hashes in `BILL_OF_MATERIALS.md` and `scripts/install.sh`. Dependency hashes verified by `verify_security.sh` (check 9). Minimal dependency surface: whisper.cpp (MIT), Ollama (MIT), SQLCipher (BSD), four Python packages. No auto-update mechanisms. |
| T7 | **Keystroke injection abuse** — CGEvent API used to inject unintended keystrokes | Low | CGEvent injection is triggered only by the post-processor pipeline after a valid hotkey-hold → transcription cycle. Injection content is the user's own speech. Hallucination guard discards LLM output that diverges >50% from the original transcript. No external input reaches the injection path. |
| T8 | **LLM prompt/response interception** — Ollama communication intercepted on the loopback interface | Low | Ollama communicates exclusively over `127.0.0.1:11434` (loopback). Interception requires root access to the local machine, which is outside Vox's threat model (a root-compromised machine has far greater attack vectors). Ollama is not exposed on any external interface. Verified by `verify_security.sh` (check 2). |

## MDM Approval Path

For IT administrators deploying Vox in MDM-managed environments (Jamf, Kandji, Mosyle):

### 1. Create a PPPC (Privacy Preferences Policy Control) profile

Grant the following Accessibility API permission to the VoxDaemon binary:

| Identifier | Identifier Type | Code Requirement | Permission | Value |
|---|---|---|---|---|
| `com.vox.daemon` | Bundle ID | `identifier "com.vox.daemon"` | Accessibility | Allow |

### 2. Deploy the profile

- **Jamf Pro**: Computers → Configuration Profiles → Privacy Preferences Policy Control → Upload the `.mobileconfig` containing the PPPC payload. Scope to target machines.
- **Kandji**: Library → Profiles → Add Profile → Privacy Preferences → Configure the Accessibility entry. Assign to the relevant blueprint.

### 3. Install Vox

Run the installation script on each target machine:

```bash
git clone https://github.com/Bogdanovist/hello-computer.git
cd hello-computer
scripts/install.sh
```

The script is idempotent and handles all dependency installation.

### 4. Verify security posture

After installation and first run, execute the security verification script:

```bash
scripts/verify_security.sh
```

All 9 checks should pass. Share the output with your security team for review.

### 5. Ongoing verification

The security verification script can be run at any time — schedule it via a Jamf/Kandji script or run on-demand during audits:

```bash
# Run as the target user (not root) to verify user-level controls
scripts/verify_security.sh
```

## Verification

Vox ships with a runtime security verification script that validates all security claims described in this document. Run it at any time:

```bash
scripts/verify_security.sh
```

The script performs 9 checks:

| # | Check | What It Verifies |
|---|---|---|
| 1 | Zero network egress (Vox) | VoxDaemon has no non-localhost network connections |
| 2 | Ollama bound to localhost | Ollama listens only on `127.0.0.1:11434` |
| 3 | Ledger encryption | `corrections.db` is unreadable by plain `sqlite3` |
| 4 | Ledger key in Keychain | `com.vox.ledger` entry exists in macOS Keychain |
| 5 | Config directory permissions | `~/.vox/` has `0700` permissions |
| 6 | Blocklist configured | `config.toml` contains non-empty `blocklist_bundle_ids` |
| 7 | No content in logs | Log file contains no transcription text markers |
| 8 | Audio temp files cleaned | No `vox_audio_*` files in `/tmp` |
| 9 | Dependency hashes | Installed versions match `BILL_OF_MATERIALS.md` |

Exit code equals the number of failed checks. Exit code 0 means all security controls are verified.
