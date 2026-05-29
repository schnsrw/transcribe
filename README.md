# Casual-SST

Lightweight, multilingual, low-hallucination live-call transcription.
Jigasi-compatible WebSocket wire format. Pluggable ASR backends routed
by language.

## At a glance

| | |
|---|---|
| Wire protocol | Jigasi-compatible — see [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| Backends | Voxtral · Parakeet · AI4Bharat IndicConformer · faster-whisper-turbo |
| Languages | 13 native + 25 EU + 8 Indic + 99 fallback |
| Code-switching | Hinglish · Tanglish · Banglish · `auto` |
| Hallucination guards | See [docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md) |
| Configs | `config/base.yaml` + `config/local.yaml` + `config/prod.yaml` |
| Runtime | Docker only — no host-side Python deps required |

## Docs

- **[CLAUDE.md](CLAUDE.md)** — orientation, invariants, conventions
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system overview + diagrams
- **[docs/CODEGRAPH.md](docs/CODEGRAPH.md)** — module map + dependency graph
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — ADRs (why we picked what we picked)
- **[docs/PROTOCOL.md](docs/PROTOCOL.md)** — wire format spec
- **[docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md)** — Whisper-turbo hardening stack

## Quickstart — Docker (recommended)

The whole stack runs in containers so nothing leaks onto the host.

```bash
# Build + run API (port 8000) and the demo (port 8080).
docker compose -f compose.dev.yaml up --build
```

Then open **http://localhost:8080** in Chrome and click *Transcribe*.

```bash
# Stop, keep the model cache for next run:
docker compose -f compose.dev.yaml down

# Wipe everything including the Hugging Face cache:
docker compose -f compose.dev.yaml down -v
```

The first `up` downloads the Whisper model (small, ~500 MB) into the
`casual-sst-hf-cache` named volume. Subsequent starts reuse it.

## Tests — Docker

```bash
# Unit + integration suite, isolated in the same image as the service:
docker compose -f compose.test.yaml run --rm tests

# Filter:
docker compose -f compose.test.yaml run --rm tests pytest -k cut_mark -v
```

E2E (Playwright) runs on the host against the dockerized server:

```bash
docker compose -f compose.dev.yaml up -d
cd tests/e2e && npm install && npx playwright test
```

## Production

```bash
CONFIG_PATH=/app/config/prod.yaml \
  docker compose -f compose.dev.yaml up --build casual-sst
```

Prod config expects:
- JWT auth enforced (`bypass_auth: false`)
- CUDA device available (override the base image to `nvidia/cuda:12.x-...`
  if you don't already)
- All four backends loaded

See [docs/ARCHITECTURE.md#failure-modes--how-we-handle-them](docs/ARCHITECTURE.md).

## Host-side dev (optional, not recommended)

If you really want to run without Docker:

```bash
poetry install
poetry run uvicorn casual_sst.main:app --host 0.0.0.0 --port 8000
```

You'll need ffmpeg < 7 in `PATH` and Python 3.11.

## License

Apache 2.0.
