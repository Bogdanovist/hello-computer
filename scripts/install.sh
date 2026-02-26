#!/bin/bash
# Vox Installation Script
# Installs all dependencies and builds Vox from source.
# Safe to run multiple times (idempotent).
set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned versions — update these when upgrading dependencies
# ---------------------------------------------------------------------------
WHISPER_CPP_COMMIT="f68298ce06ca0c50d09e3e0e07fc60b99e4fac55"  # v1.7.3
WHISPER_MODEL="ggml-large-v3-turbo.bin"
WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${WHISPER_MODEL}"
OPEN_WISPR_COMMIT="main"
OLLAMA_MODEL="qwen3:8b"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="${REPO_DIR}/vendor"
VOX_DIR="${HOME}/.vox"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "==> $*"; }
warn()  { echo "  WARN  $*"; }
err()   { echo "  ERROR $*" >&2; }
ok()    { echo "  OK    $*"; }

die() {
    err "$@"
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

# macOS only
if [ "$(uname -s)" != "Darwin" ]; then
    die "Vox requires macOS. Detected: $(uname -s)"
fi

# Apple Silicon (arm64)
if [ "$(uname -m)" != "arm64" ]; then
    die "Vox requires Apple Silicon (arm64). Detected: $(uname -m)"
fi

# macOS version >= 14 (Sonoma)
macos_version=$(sw_vers -productVersion)
macos_major=$(echo "$macos_version" | cut -d. -f1)
if [ "$macos_major" -lt 14 ]; then
    die "Vox requires macOS 14 (Sonoma) or later. Detected: $macos_version"
fi
ok "macOS $macos_version on Apple Silicon"

# Xcode Command Line Tools
if ! xcode-select -p &>/dev/null; then
    err "Xcode Command Line Tools not installed."
    echo "    Run: xcode-select --install"
    exit 1
fi
ok "Xcode Command Line Tools installed"

# RAM check (warn if < 32 GB)
ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
ram_gb=$((ram_bytes / 1073741824))
if [ "$ram_gb" -lt 32 ]; then
    warn "Detected ${ram_gb}GB RAM. 32GB+ recommended for Qwen3 8B model performance."
else
    ok "${ram_gb}GB RAM detected"
fi

# ---------------------------------------------------------------------------
# 2. Homebrew
# ---------------------------------------------------------------------------
info "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv)"
    ok "Homebrew installed"
else
    ok "Homebrew already installed"
fi

# ---------------------------------------------------------------------------
# 3. SQLCipher
# ---------------------------------------------------------------------------
info "Checking SQLCipher..."
if ! brew list sqlcipher &>/dev/null; then
    info "Installing SQLCipher via Homebrew..."
    brew install sqlcipher
    ok "SQLCipher installed"
else
    ok "SQLCipher already installed"
fi

# ---------------------------------------------------------------------------
# 4. uv (Python package manager)
# ---------------------------------------------------------------------------
info "Checking uv..."
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for this session
    export PATH="${HOME}/.local/bin:${PATH}"
    ok "uv installed"
else
    ok "uv already installed"
fi

# ---------------------------------------------------------------------------
# 5. Ollama
# ---------------------------------------------------------------------------
info "Checking Ollama..."
if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installed"
else
    ok "Ollama already installed"
fi

# Pull the model (idempotent — skips if already present)
info "Pulling Ollama model: ${OLLAMA_MODEL}..."
ollama pull "$OLLAMA_MODEL"
ok "Model ${OLLAMA_MODEL} available"

# ---------------------------------------------------------------------------
# 6. whisper.cpp
# ---------------------------------------------------------------------------
info "Setting up whisper.cpp..."
mkdir -p "$VENDOR_DIR"

if [ -d "${VENDOR_DIR}/whisper.cpp" ]; then
    # Already cloned — verify pinned commit
    actual_commit=$(git -C "${VENDOR_DIR}/whisper.cpp" rev-parse HEAD 2>/dev/null || echo "")
    if [ "$actual_commit" = "$WHISPER_CPP_COMMIT" ]; then
        ok "whisper.cpp already at pinned commit"
    else
        info "Updating whisper.cpp to pinned commit..."
        git -C "${VENDOR_DIR}/whisper.cpp" fetch origin
        git -C "${VENDOR_DIR}/whisper.cpp" checkout "$WHISPER_CPP_COMMIT"
    fi
else
    info "Cloning whisper.cpp..."
    git clone https://github.com/ggerganov/whisper.cpp.git "${VENDOR_DIR}/whisper.cpp"
    git -C "${VENDOR_DIR}/whisper.cpp" checkout "$WHISPER_CPP_COMMIT"
fi

# Build whisper.cpp with Metal support
info "Building whisper.cpp with Metal..."
make -C "${VENDOR_DIR}/whisper.cpp" -j "$(sysctl -n hw.logicalcpu)" WHISPER_METAL=1
ok "whisper.cpp built with Metal support"

# Install headers and library to /usr/local so Swift can find them
info "Installing whisper.cpp headers and library..."
sudo mkdir -p /usr/local/include /usr/local/lib
sudo cp "${VENDOR_DIR}/whisper.cpp/include/whisper.h" /usr/local/include/
sudo cp "${VENDOR_DIR}/whisper.cpp/libwhisper.a" /usr/local/lib/ 2>/dev/null \
    || sudo cp "${VENDOR_DIR}/whisper.cpp/libwhisper.dylib" /usr/local/lib/ 2>/dev/null \
    || true
# Also copy shared lib if it exists
if [ -f "${VENDOR_DIR}/whisper.cpp/libwhisper.dylib" ]; then
    sudo cp "${VENDOR_DIR}/whisper.cpp/libwhisper.dylib" /usr/local/lib/
fi
ok "whisper.cpp installed to /usr/local"

# Download whisper model
MODELS_DIR="${VENDOR_DIR}/whisper.cpp/models"
if [ -f "${MODELS_DIR}/${WHISPER_MODEL}" ]; then
    ok "Whisper model already downloaded"
else
    info "Downloading whisper model: ${WHISPER_MODEL}..."
    mkdir -p "$MODELS_DIR"
    curl -L --progress-bar -o "${MODELS_DIR}/${WHISPER_MODEL}" "$WHISPER_MODEL_URL"
    ok "Whisper model downloaded"
fi

# ---------------------------------------------------------------------------
# 7. open-wispr fork
# ---------------------------------------------------------------------------
info "Setting up open-wispr..."
if [ -d "${VENDOR_DIR}/open-wispr" ]; then
    actual_commit=$(git -C "${VENDOR_DIR}/open-wispr" rev-parse HEAD 2>/dev/null || echo "")
    if [ "$OPEN_WISPR_COMMIT" = "main" ]; then
        ok "open-wispr already cloned"
    elif [ "$actual_commit" = "$OPEN_WISPR_COMMIT" ]; then
        ok "open-wispr already at pinned commit"
    else
        info "Updating open-wispr to pinned commit..."
        git -C "${VENDOR_DIR}/open-wispr" fetch origin
        git -C "${VENDOR_DIR}/open-wispr" checkout "$OPEN_WISPR_COMMIT"
    fi
else
    info "Cloning open-wispr..."
    git clone https://github.com/Bogdanovist/open-wispr.git "${VENDOR_DIR}/open-wispr"
    if [ "$OPEN_WISPR_COMMIT" != "main" ]; then
        git -C "${VENDOR_DIR}/open-wispr" checkout "$OPEN_WISPR_COMMIT"
    fi
fi
ok "open-wispr ready"

# ---------------------------------------------------------------------------
# 8. Build Swift components
# ---------------------------------------------------------------------------
info "Building Swift components..."
cd "${REPO_DIR}/swift"
swift build -c release \
    -Xcc -I/usr/local/include \
    -Xlinker -L/usr/local/lib
ok "Swift components built"

# ---------------------------------------------------------------------------
# 9. Python environment
# ---------------------------------------------------------------------------
info "Setting up Python environment..."
cd "${REPO_DIR}/python"
uv sync
ok "Python environment ready"

# ---------------------------------------------------------------------------
# 10. Create ~/.vox/ directory
# ---------------------------------------------------------------------------
info "Setting up Vox config directory..."
mkdir -p "$VOX_DIR"
chmod 700 "$VOX_DIR"

if [ ! -f "${VOX_DIR}/config.toml" ]; then
    cp "${REPO_DIR}/config/default.toml" "${VOX_DIR}/config.toml"
    ok "Default config copied to ${VOX_DIR}/config.toml"
else
    ok "Config already exists at ${VOX_DIR}/config.toml"
fi
ok "~/.vox/ directory ready (permissions: 0700)"

# ---------------------------------------------------------------------------
# Done — post-install instructions
# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo "  Vox installation complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. Grant Accessibility API permission:"
echo "     - Open System Settings > Privacy & Security > Accessibility"
echo "     - Add VoxDaemon to the allowed list"
echo "     - Or ask IT to deploy the PPPC profile via Jamf/Kandji"
echo ""
echo "  2. Start the Vox daemon:"
echo "     ${REPO_DIR}/swift/.build/release/VoxDaemon"
echo ""
echo "  3. Verify security posture:"
echo "     ${REPO_DIR}/scripts/verify_security.sh"
echo ""
echo "  4. Hold the Globe key and speak to dictate."
echo ""
