#!/usr/bin/env bash
# Casual-SST — Apple Silicon host-mode dev launcher.
#
# Creates / reuses a self-contained .venv-mac/ in the repo root,
# installs the host-side deps (faster-whisper for LID, silero-vad for
# VAD, mlx-whisper for the GPU-accelerated transcription backend), then
# runs uvicorn against config/dev-mac.yaml.
#
# Why host-mode: Docker Desktop on macOS cannot expose Apple GPU/ANE to
# containers. To use the M-series GPU we have to run on the host.
#
# Residue: everything lands in .venv-mac/ inside the repo. `make clean`
# wipes it.

set -euo pipefail
cd "$(dirname "$0")/.."

# -----------------------------------------------------------------------------
# 1. Pick a Python. We need ≥ 3.11 (matches Docker image + pyproject.toml).
#    macOS ships 3.9.x by default at /usr/bin/python3; users typically have
#    a newer one via homebrew or pyenv. Override with $PYTHON if needed.
# -----------------------------------------------------------------------------
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver=$("$candidate" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "(0,0)")
            case "$ver" in
                "(3,11)"|"(3,12)"|"(3,13)") PYTHON="$candidate"; break ;;
            esac
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "[dev-mac] Need Python ≥ 3.11. Install with: brew install python@3.12" >&2
    echo "[dev-mac] Or set PYTHON=path/to/python before running." >&2
    exit 1
fi
echo "[dev-mac] Using $PYTHON ($($PYTHON --version 2>&1))"

VENV=".venv-mac"
ACTIVATE="$VENV/bin/activate"

if [ ! -d "$VENV" ]; then
    echo "[dev-mac] Creating venv at $VENV/"
    "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1090
. "$ACTIVATE"

# -----------------------------------------------------------------------------
# 2. Install deps. Pinned to the same ranges as the Docker image so behavior
#    is comparable. mlx-whisper is the Apple-only extra.
# -----------------------------------------------------------------------------
echo "[dev-mac] Installing / updating deps (first run takes a few minutes)..."
pip install --quiet --upgrade pip wheel setuptools

pip install --quiet \
    "fastapi>=0.115,<0.116" \
    "uvicorn[standard]>=0.30,<0.32" \
    "wsproto>=1.2" \
    "pydantic>=2,<3" \
    "pyyaml>=6,<7" \
    "numpy>=1.26,<2" \
    "silero-vad>=5,<6" \
    "faster-whisper>=1.1,<2" \
    "uuid6>=2024.7.10" \
    "PyJWT[crypto]>=2.8" \
    "httpx>=0.27" \
    "mlx-whisper>=0.4.1" \
    "pywhispercpp>=1.4.0"

# -----------------------------------------------------------------------------
# 3. Launch uvicorn with the Mac config. PYTHONPATH lets it import
#    `casual_sst.*` from src/ without an editable install.
# -----------------------------------------------------------------------------
export PYTHONPATH="$PWD/src"
# CONFIG override lets `make dev-mac-cpp` use config/dev-mac-cpp.yaml
# while reusing this single launcher script.
export CONFIG_PATH="$PWD/${CONFIG:-config/dev-mac.yaml}"

PORT="${PORT:-8100}"
echo "[dev-mac] Casual-SST on http://localhost:${PORT}  (Ctrl-C to stop)"
echo "[dev-mac] Config: $CONFIG_PATH"
echo "[dev-mac] Backend: mlx_whisper on Apple Metal"
echo ""

# uvicorn 0.31's bundled `websockets` legacy impl is incompatible with
# `websockets` library ≥ 14. We install `wsproto` above and pin uvicorn
# to use it — works with any websockets version.
#
# `--reload` is OFF by default because it tears down the loaded MLX
# model on every file change, which adds 5-10 s of Metal-kernel warm-up
# to the next request. Set RELOAD=1 to enable it while iterating on
# server code.
RELOAD_ARGS=()
if [ "${RELOAD:-0}" = "1" ]; then
    RELOAD_ARGS+=(--reload --reload-dir src)
    echo "[dev-mac] --reload enabled (will reload on src/ changes)"
fi

exec uvicorn casual_sst.main:app \
    --host 0.0.0.0 --port "$PORT" \
    --ws wsproto \
    "${RELOAD_ARGS[@]}"
