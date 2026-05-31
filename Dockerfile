# syntax=docker/dockerfile:1.7
# ----------------------------------------------------------------------------
# Casual-SST — CPU image (slim).
#
# This image is for: cross-platform dev, CI, and CPU-only deployments.
# For GPU production use Dockerfile.cuda + compose.prod-cuda.yaml.
#
# Goals
# -----
# * Slim runtime. Earlier builds ballooned to 9 GB because torch's
#   default PyPI wheel pulled cuda-toolkit + nvidia-* even on CPU
#   hosts. We pin torch to the dedicated CPU wheel index instead — that
#   alone drops ~7 GB.
# * ffmpeg + libsndfile only — the bare minimum faster-whisper/silero-vad
#   need at runtime. No build-essential in the runtime stage.
# * Model weights NEVER baked in — they download into the HF cache,
#   which compose mounts as a named volume so rebuilds don't re-download.
# * Tests run in the same image (no Python deps on the host).
#
# Build:
#   docker build -t casual-sst:dev .
# ----------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11

# ============================================================================
# Stage 1: builder
# ============================================================================
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_PREFER_BINARY=1

# Build-time system packages. ffmpeg + libsndfile because some Python
# wheels link against libav* at build time; build-essential for any
# wheel that has to compile a small C extension.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip wheel setuptools

# 1) torch CPU wheel FIRST from PyTorch's CPU index. This avoids the
#    GPU torch (and its ~6 GB of nvidia-* / cuda-* transitive packages)
#    that would otherwise be selected by default on PyPI.
RUN pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        "torch>=2.4,<3"

# 2) The rest of the stack. silero-vad will see torch already installed
#    and won't pull the GPU build in transitively.
RUN pip install \
        "fastapi>=0.115,<0.116" \
        "uvicorn[standard]>=0.30,<0.32" \
        "wsproto>=1.2" \
        "pydantic>=2,<3" \
        "pyyaml>=6,<7" \
        "numpy>=1.26,<2" \
        "silero-vad>=5,<6" \
        "faster-whisper>=1.1,<2" \
        "uuid6>=2024.7.10"


# ============================================================================
# Stage 2: runtime
# ============================================================================
FROM python:${PYTHON_VERSION}-slim AS runtime

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

RUN useradd --create-home --uid 1001 --shell /bin/bash casual

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=casual:casual src /app/src
COPY --chown=casual:casual config /app/config
COPY --chown=casual:casual pyproject.toml /app/pyproject.toml

RUN mkdir -p /cache && chown -R casual:casual /cache /app

USER casual
EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "casual_sst.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "wsproto"]
