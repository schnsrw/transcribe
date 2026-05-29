# syntax=docker/dockerfile:1.7
# ----------------------------------------------------------------------------
# Casual-SST — single Dockerfile, multi-stage.
#
# Goals:
#   * Slim runtime (~300 MB without models)
#   * ffmpeg installed (faster-whisper requires libav*)
#   * Model weights are NEVER baked in — they download into the
#     Hugging Face cache, which is mounted as a named volume by compose
#     so iterating on the image does not re-download.
#   * Tests run in the same image (no Python deps on the host).
# ----------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11
ARG POETRY_VERSION=1.8.3

# ---------- Stage 1: builder ----------
# Build wheels / install deps into an isolated venv so the runtime stage
# can copy just the venv without dragging compilers along.
FROM python:${PYTHON_VERSION}-slim AS builder

ARG POETRY_VERSION
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Build-time system packages. We install ffmpeg *here* too because some
# Python deps (notably faster-whisper) link against libav* at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libsndfile1 \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app
COPY pyproject.toml ./
# Only install the base group. The voxtral / parakeet / indic optional
# groups carry GPU-heavy deps (vLLM, NeMo) that are wired up later from
# config/prod.yaml — they are not needed for local dev or CI.
RUN poetry install --no-root --without dev --only main \
    && rm -rf $POETRY_CACHE_DIR


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
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/cache/huggingface \
    XDG_CACHE_HOME=/cache \
    CONFIG_PATH=/app/config/local.yaml

# Non-root user — runtime never needs to write outside /cache (mounted).
RUN useradd --create-home --uid 1001 --shell /bin/bash casual

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
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
