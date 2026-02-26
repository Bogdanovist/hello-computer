#!/bin/bash
# Vox Security Verification Script
# Checks all runtime security controls. Run at any time to verify security posture.
# Exit code = number of FAILed checks (0 = all passed).
set -euo pipefail

PASS=0
FAIL=0
WARN=0

VOX_DIR="${HOME}/.vox"
LEDGER_DB="${VOX_DIR}/corrections.db"
LOG_FILE="${VOX_DIR}/vox.log"
CONFIG_FILE="${VOX_DIR}/config.toml"
BOM_FILE="$(cd "$(dirname "$0")/.." && pwd)/BILL_OF_MATERIALS.md"

check() {
    local name="$1"
    local result="$2"
    local detail="${3:-}"
    if [ "$result" = "PASS" ]; then
        echo "  PASS  $name"
        PASS=$((PASS + 1))
    elif [ "$result" = "WARN" ]; then
        echo "  WARN  $name${detail:+ — $detail}"
        WARN=$((WARN + 1))
    elif [ "$result" = "SKIP" ]; then
        echo "  SKIP  $name${detail:+ — $detail}"
    else
        echo "  FAIL  $name${detail:+ — $detail}"
        FAIL=$((FAIL + 1))
    fi
}

echo "Vox Security Verification"
echo "========================="
echo ""

# --------------------------------------------------------------------------
# 1. Zero network egress — Vox processes should have no non-localhost connections
# --------------------------------------------------------------------------
if pgrep -qf "VoxDaemon" 2>/dev/null; then
    vox_connections=$(lsof -i -P -n 2>/dev/null | grep -i "VoxDaemon" | grep -v "127\.0\.0\.1" | grep -v "localhost" | grep -v "\[::1\]" || true)
    if [ -z "$vox_connections" ]; then
        check "Zero network egress (Vox)" "PASS"
    else
        check "Zero network egress (Vox)" "FAIL" "non-localhost connections found"
    fi
else
    check "Zero network egress (Vox)" "SKIP" "VoxDaemon not running"
fi

# --------------------------------------------------------------------------
# 2. Ollama bound to localhost only
# --------------------------------------------------------------------------
if pgrep -qf "ollama" 2>/dev/null; then
    ollama_bindings=$(lsof -i -P -n 2>/dev/null | grep -i "ollama" | grep "LISTEN" || true)
    non_local=$(echo "$ollama_bindings" | grep -v "127\.0\.0\.1" | grep -v "localhost" | grep -v "\[::1\]" | grep -v "^\s*$" || true)
    if [ -z "$non_local" ]; then
        check "Ollama bound to localhost only" "PASS"
    else
        check "Ollama bound to localhost only" "FAIL" "non-localhost bindings found"
    fi
else
    check "Ollama bound to localhost only" "SKIP" "Ollama not running"
fi

# --------------------------------------------------------------------------
# 3. Ledger encryption — sqlite3 without key should fail to read
# --------------------------------------------------------------------------
if [ -f "$LEDGER_DB" ]; then
    if command -v sqlite3 &>/dev/null; then
        # Attempt to read with plain sqlite3 (no encryption key)
        read_result=$(sqlite3 "$LEDGER_DB" "SELECT count(*) FROM sqlite_master;" 2>&1 || true)
        if echo "$read_result" | grep -qi "not a database\|encrypted\|error\|malformed"; then
            check "Ledger encryption (sqlite3 cannot read)" "PASS"
        else
            check "Ledger encryption (sqlite3 cannot read)" "FAIL" "database readable without key"
        fi
    else
        check "Ledger encryption (sqlite3 cannot read)" "SKIP" "sqlite3 not installed"
    fi
else
    check "Ledger encryption (sqlite3 cannot read)" "SKIP" "ledger not yet created"
fi

# --------------------------------------------------------------------------
# 4. Ledger key in macOS Keychain
# --------------------------------------------------------------------------
if command -v security &>/dev/null; then
    if security find-generic-password -s "com.vox.ledger" &>/dev/null; then
        check "Ledger key in Keychain" "PASS"
    else
        if [ -f "$LEDGER_DB" ]; then
            check "Ledger key in Keychain" "FAIL" "ledger exists but no Keychain entry"
        else
            check "Ledger key in Keychain" "SKIP" "ledger not yet created"
        fi
    fi
else
    check "Ledger key in Keychain" "SKIP" "not on macOS (no security command)"
fi

# --------------------------------------------------------------------------
# 5. Config directory permissions — must be 0700
# --------------------------------------------------------------------------
if [ -d "$VOX_DIR" ]; then
    if stat --version &>/dev/null 2>&1; then
        # GNU stat (Linux)
        dir_perms=$(stat -c "%a" "$VOX_DIR")
    else
        # BSD stat (macOS)
        dir_perms=$(stat -f "%Lp" "$VOX_DIR")
    fi
    if [ "$dir_perms" = "700" ]; then
        check "Config directory permissions (0700)" "PASS"
    else
        check "Config directory permissions (0700)" "FAIL" "permissions are $dir_perms"
    fi
else
    check "Config directory permissions (0700)" "SKIP" "~/.vox/ does not exist"
fi

# --------------------------------------------------------------------------
# 6. Blocklist configured — config should have non-empty blocklist_bundle_ids
# --------------------------------------------------------------------------
if [ -f "$CONFIG_FILE" ]; then
    # Check that blocklist_bundle_ids exists and has at least one entry
    blocklist_entries=$(grep -cE '^\s*"com\.' "$CONFIG_FILE" 2>/dev/null || true)
    if [ "$blocklist_entries" -gt 0 ] 2>/dev/null; then
        check "Blocklist configured" "PASS"
    else
        check "Blocklist configured" "WARN" "blocklist_bundle_ids appears empty"
    fi
else
    check "Blocklist configured" "WARN" "config file not found at $CONFIG_FILE"
fi

# --------------------------------------------------------------------------
# 7. No content in logs — log file must not contain transcription text markers
# --------------------------------------------------------------------------
if [ -f "$LOG_FILE" ]; then
    # Search for AX value content, raw transcription, or correction text in logs.
    # These patterns indicate content leakage; metadata-only logs should never match.
    content_matches=$(grep -ciE "AXValue|transcript_text|corrected_text|raw_text" "$LOG_FILE" 2>/dev/null || true)
    if [ "$content_matches" = "0" ] || [ -z "$content_matches" ]; then
        check "No content in logs" "PASS"
    else
        check "No content in logs" "FAIL" "$content_matches lines with content markers"
    fi
else
    check "No content in logs" "SKIP" "log file not found"
fi

# --------------------------------------------------------------------------
# 8. Audio temp files cleaned — no lingering vox audio files in /tmp
# --------------------------------------------------------------------------
audio_files=$(find /tmp -maxdepth 1 -name "vox_audio_*" 2>/dev/null || true)
if [ -z "$audio_files" ]; then
    check "Audio temp files cleaned" "PASS"
else
    file_count=$(echo "$audio_files" | wc -l | tr -d ' ')
    check "Audio temp files cleaned" "WARN" "$file_count file(s) found (may be in-progress dictation)"
fi

# --------------------------------------------------------------------------
# 9. Dependency hashes match BILL_OF_MATERIALS.md
# --------------------------------------------------------------------------
if [ -f "$BOM_FILE" ]; then
    bom_ok=true

    # Check whisper.cpp commit hash
    if [ -d "$(cd "$(dirname "$0")/.." && pwd)/vendor/whisper.cpp" ]; then
        whisper_dir="$(cd "$(dirname "$0")/.." && pwd)/vendor/whisper.cpp"
        actual_commit=$(git -C "$whisper_dir" rev-parse HEAD 2>/dev/null || echo "unknown")
        expected_commit=$(grep -A1 "whisper.cpp" "$BOM_FILE" | grep -oE '[a-f0-9]{7,40}' | head -1 || true)
        if [ -n "$expected_commit" ] && [ "$actual_commit" != "unknown" ]; then
            if [[ "$actual_commit" == "$expected_commit"* ]] || [[ "$expected_commit" == "$actual_commit"* ]]; then
                : # match
            else
                bom_ok=false
            fi
        fi
    fi

    # Check Ollama version
    if command -v ollama &>/dev/null; then
        actual_ollama=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
        expected_ollama=$(grep -A1 "Ollama" "$BOM_FILE" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 | sed 's/^v//' || true)
        if [ -n "$expected_ollama" ] && [ -n "$actual_ollama" ] && [ "$actual_ollama" != "$expected_ollama" ]; then
            bom_ok=false
        fi
    fi

    if [ "$bom_ok" = true ]; then
        check "Dependency hashes match BILL_OF_MATERIALS.md" "PASS"
    else
        check "Dependency hashes match BILL_OF_MATERIALS.md" "FAIL" "version mismatch detected"
    fi
else
    check "Dependency hashes match BILL_OF_MATERIALS.md" "SKIP" "BILL_OF_MATERIALS.md not found"
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
echo ""
echo "Security verification: $PASS passed, $FAIL failed, $WARN warnings"
exit "$FAIL"
