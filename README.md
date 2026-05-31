# Casual-SST

Lightweight, multilingual, low-hallucination live-call transcription.
Jigasi-compatible WebSocket wire format. Pluggable ASR backends routed
by language.

## At a glance

| | |
|---|---|
| Wire protocol | Jigasi-compatible — see [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| Backends | mlx-whisper (Mac dev) · whisper.cpp+Metal (Mac dev alt) · faster-whisper (Linux + prod) · Voxtral · Parakeet · AI4Bharat IndicConformer (stubs) |
| Languages | 13 native + 25 EU + 8 Indic + 99 fallback |
| Code-switching | Hinglish · Tanglish · Banglish · `auto` |
| Hallucination guards | See [docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md) |
| Configs | `base.yaml` + `local.yaml` (Docker dev) / `dev-mac.yaml` / `dev-mac-cpp.yaml` / `dev-linux.yaml` / `prod.yaml` |
| Operations | `/healthz` (liveness) · `/health` (readiness) · `/metrics` (Prometheus) · `/admin/` (dashboard, token-gated) |
| Optional LLM | `POST /api/summarize` — OpenAI-format or Ollama backends |

## Run matrix

| Target | Where | Backend | When |
|---|---|---|---|
| `make dev-mac` | host (Apple Silicon) | **mlx-whisper on Metal GPU** | Daily Mac dev. ≈7× faster than Docker CPU. |
| `make dev-linux` | host (Linux) | faster-whisper + CUDA | Linux dev box, matches prod. |
| `make dev-docker` | containers | faster-whisper CPU | Cross-platform / CI. Slow on Mac. |
| `make test` | containers | (mocks) | Unit + integration suite. |
| `make prod` | containers (Linux + CUDA) | faster-whisper | Production. JWT enforced. |

`make help` lists targets. `make clean` wipes the host venvs (`.venv-mac/`
and `.venv-linux/`); Docker volumes are untouched.

## Quickstart on a Mac

```bash
make dev-mac
# First run installs deps + downloads whisper-large-v3-turbo-q4 MLX
# checkpoint (~800 MB) into the Hugging Face cache. ~5 min.
# Subsequent runs start in seconds.
```

Then point a client at `ws://localhost:8100/ws/<meeting_id>`. The
browser demo is served by the `make dev-docker` stack on `:8180` —
you can run both at the same time and just set the demo's WS host
input to `ws://localhost:8100/ws`.

**Measured perf** (M4 base, whisper-small-mlx, warm model):

| | First event | Total |
|---|---|---|
| EN WHOLE (5 s audio) | **0.6 s** | 1.3 s |
| EN STREAM-1s (5 chunks) | **4.0 s** | 9.7 s |
| HI WHOLE (2 s audio) | 0.7 s | 2.0 s |
| HI STREAM-1s (2 chunks) | 21 s | 22 s |

Mac dev is the right path for English streaming and all-language batch
iteration. **Hindi STREAM** has per-call MLX overhead that doesn't
amortize on M4 base — use `make dev-docker` for realistic 1 s-chunk
non-English simulation. See [docs/DECISIONS.md#adr-013](docs/DECISIONS.md)
for the full measurement matrix + rationale.

## Quickstart with Docker (works everywhere)

```bash
make dev-docker     # API on :8000, demo on :8080
make stop           # tear down, keep the model cache
```

The first build downloads whisper-small (~500 MB) into the
`casual-sst-hf-cache` named volume.

## Docs

- **[docs/SETUP.md](docs/SETUP.md)** — **setup guide for Mac dev / Linux dev / Docker / production**
- **[docs/DOCKER_GPU.md](docs/DOCKER_GPU.md)** — Docker + NVIDIA GPU on Linux (driver, toolkit, troubleshooting)
- **[CLAUDE.md](CLAUDE.md)** — orientation, invariants, conventions
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system overview + diagrams
- **[docs/CODEGRAPH.md](docs/CODEGRAPH.md)** — module map + dependency graph
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — ADRs (017 entries)
- **[docs/PROTOCOL.md](docs/PROTOCOL.md)** — wire format spec
- **[docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md)** — Whisper hardening stack
- **[tests/live/README.md](tests/live/README.md)** — live test harness
- **[.env.example](.env.example)** — every environment variable, documented

## Tests

```bash
make test           # unit + integration in Docker
```

E2E (Playwright) — see [tests/e2e/README.md](tests/e2e/README.md).
Live case suite — see [tests/live/README.md](tests/live/README.md).
Mac↔Docker parity check — `tests/live/probes/parity.py`.

## Production

```bash
make prod
```

Expects: JWT enforced, CUDA available, `prod.yaml` mounted.

Pull the prebuilt image from Docker Hub instead of building locally:

```bash
docker pull schnsrw/casual-sst:cuda-latest
# or pin a specific version
docker pull schnsrw/casual-sst:cuda-0.2.0
```

The CPU image is also published — useful for non-GPU servers:

```bash
docker pull schnsrw/casual-sst:latest          # CPU, multi-arch (amd64+arm64)
docker pull schnsrw/casual-sst:0.2.0           # CPU, pinned
```

## Releasing

Push a semver tag to trigger the release pipeline
(`.github/workflows/release.yml`):

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

This runs the full test suite (release gate), then builds + pushes
both the CPU (linux/amd64 + linux/arm64) and CUDA (linux/amd64) images
to Docker Hub with semver tags + `latest` / `cuda-latest` aliases.

**One-time GitHub setup**:

1. Create an environment called `release`
   (Settings → Environments → New environment).
2. Inside that environment, add two **secrets**:

   | Name | Value |
   |---|---|
   | `DOCKERHUB_USERNAME` | your Docker Hub username |
   | `DOCKERHUB_TOKEN` | Docker Hub access token with read+write on `casual-sst` |

3. (Optional) Add a repo-level **variable** `DOCKERHUB_REPO` if you
   want the image published under a name other than `casual-sst`.
4. (Optional) Enable environment protection rules (manual approval,
   tag-only deploys, etc.) on the `release` environment — the
   workflow respects them automatically.

## License

Apache 2.0.
