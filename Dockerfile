# syntax=docker/dockerfile:1.7
# ----------------------------------------------------------------------------
# Casual-SST — single Dockerfile, multi-stage.
#
# Goals:
#   * Slim runtime (~700 MB with torch CPU + ctranslate2)
#   * ffmpeg installed (faster-whisper requires libav*)
#   * Model weights are NEVER baked in — they download into the
#     Hugging Face cache, which is mounted as a named volume by compose
#     so iterating on the image does not re-download.
#   * Tests run in the same image (no Python deps on the host).
#
# We use plain pip rather than poetry because poetry's resolver gets
# stuck exploring legacy versions of unrelated transitive deps
# (huggingface_hub → datasets old releases). Pinning at the install
# layer with explicit version ranges keeps build time predictable.
# ----------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11

# ---------- Stage 1: builder ----------
# Build an isolated venv so the runtime stage can copy just /opt/venv
# without dragging build tools along.
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_PREFER_BINARY=1

# Build-time system packages. ffmpeg + libsndfile are runtime libs that
# silero-vad / faster-whisper need; build-essential covers any wheel
# that has to compile a small C extension.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip + install wheel/build first so binary wheels are preferred.
RUN pip install --upgrade pip wheel setuptools

# Casual-SST runtime deps. CPU-only torch is pulled in transitively by
# silero-vad. Versions are pinned to ranges that match pyproject.toml.
RUN pip install \
        "fastapi>=0.115,<0.116" \
        "uvicorn[standard]>=0.30,<0.32" \
        "pydantic>=2,<3" \
        "pyyaml>=6,<7" \
        "numpy>=1.26,<2" \
        "silero-vad>=5,<6" \
        "faster-whisper>=1.1,<2" \
        "uuid6>=2024.7.10"


# ---------- Stage 2: runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Runtime system packages — ffmpeg + libsndfile only.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/cache/huggingface \
    XDG_CACHE_HOME=/cache \
    CONFIG_PATH=/app/config/local.yaml

# Non-root user — runtime never needs to write outside /cache (mounted).
RUN useradd --create-home --uid 1001 --shell /bin/bash casual

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=casual:casual src /app/src
COPY --chown=casual:casual config /app/config
COPY --chown=casual:casual pyproject.toml /app/pyproject.toml

# /cache is the mount point for the Hugging Face cache so model weights
# persist across container rebuilds. Owned by the runtime user.
RUN mkdir -p /cache && chown -R casual:casual /cache /app

USER casual
EXPOSE 8000

# tini = PID 1, reaps zombies and forwards signals to uvicorn.
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "casual_sst.main:app", "--host", "0.0.0.0", "--port", "8000"]
