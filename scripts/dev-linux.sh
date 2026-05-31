#!/usr/bin/env bash
# Casual-SST — Linux host-mode dev launcher.
#
# Creates / reuses .venv-linux/, installs faster-whisper, runs uvicorn
# against config/dev-linux.yaml. If a CUDA GPU is detected
# (nvidia-smi succeeds), faster-whisper's `device: auto` setting will
# use it; otherwise it falls back to CPU.
#
# For Linux *prod* deployment use `make prod` (Docker + JWT), not this.

set -euo pipefail
cd "$(dirname "$0")/.."

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
    echo "[dev-linux] Need Python ≥ 3.11. Install via your distro or pyenv." >&2
    exit 1
fi
echo "[dev-linux] Using $PYTHON ($($PYTHON --version 2>&1))"

VENV=".venv-linux"
if [ ! -d "$VENV" ]; then
    echo "[dev-linux] Creating venv at $VENV/"
    "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1090
. "$VENV/bin/activate"

echo "[dev-linux] Installing / updating deps..."
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
    "uuid6>=2024.7.10"

# CUDA detection — informational only; faster-whisper's `device: auto`
# does the actual probing.
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits | head -1)
    echo "[dev-linux] CUDA GPU detected: $GPU_NAME"
else
    echo "[dev-linux] No NVIDIA GPU detected — running CPU only."
fi

export PYTHONPATH="$PWD/src"
export CONFIG_PATH="$PWD/config/dev-linux.yaml"

PORT="${PORT:-8100}"
echo "[dev-linux] Casual-SST on http://localhost:${PORT}  (Ctrl-C to stop)"
echo "[dev-linux] Config: $CONFIG_PATH"
echo ""

exec uvicorn casual_sst.main:app \
    --host 0.0.0.0 --port "$PORT" \
    --ws wsproto \
    --reload --reload-dir src
