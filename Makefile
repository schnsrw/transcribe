# Casual-SST run targets.
# ----------------------------------------------------------------------------
# Single entry point covering the four platform × env squares:
#
#   make dev-mac     host-mode on Apple Silicon (Metal GPU via mlx-whisper)
#   make dev-linux   host-mode on Linux (NVIDIA CUDA if present, else CPU)
#   make dev-docker  container mode (cross-platform, CPU only) — works
#                    everywhere; what CI uses; slow on Mac but reliable.
#   make test        unit + integration tests in Docker
#   make prod        production stack (Linux + CUDA + JWT enforced)
#
# Why three "dev" targets: ADR-013. Docker on macOS cannot pass through
# Metal/ANE, so Mac dev MUST run on the host to get GPU acceleration.
# Linux dev can use either path (host for GPU, Docker for prod parity).
# ----------------------------------------------------------------------------

.PHONY: help dev-mac dev-linux dev-docker test prod stop logs clean

help:  ## Show available targets
	@awk 'BEGIN{FS=":.*?## "} /^[a-zA-Z_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev-mac:  ## Host venv on Apple Silicon, mlx-whisper on Metal GPU
	@./scripts/dev-mac.sh

dev-linux:  ## Host venv on Linux, faster-whisper (CUDA if available)
	@./scripts/dev-linux.sh

dev-docker:  ## Containerised dev stack (API + demo nginx); CPU only
	docker compose -f compose.dev.yaml up --build

stop:  ## Stop the docker dev stack (keeps the hf-cache volume)
	docker compose -f compose.dev.yaml down

test:  ## Run unit + integration tests inside Docker
	docker compose -f compose.test.yaml run --rm tests

prod:  ## Production stack — JWT + CUDA via config/prod.yaml
	CONFIG_PATH=/app/config/prod.yaml docker compose -f compose.dev.yaml up -d

logs:  ## Tail the casual-sst container logs
	docker logs -f casual-sst

clean:  ## Wipe host venvs (.venv-mac/ and .venv-linux/)
	rm -rf .venv-mac .venv-linux
	@echo "Removed host venvs. Docker volumes untouched."
