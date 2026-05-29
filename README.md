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
| Code-switching | Hinglish · Tanglish · Banglish · auto |
| Hallucination guards | See [docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md) |

## Docs

- **[CLAUDE.md](CLAUDE.md)** — orientation, invariants, conventions
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system overview + diagrams
- **[docs/CODEGRAPH.md](docs/CODEGRAPH.md)** — module map + dependency graph
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — ADRs (why we picked what we picked)
- **[docs/PROTOCOL.md](docs/PROTOCOL.md)** — wire format spec
- **[docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md)** — Whisper-turbo hardening stack

## Quick start (local dev, CPU)

```bash
poetry install
poetry run uvicorn casual_sst.main:app --host 0.0.0.0 --port 8000
```

Default config: `config/local.yaml` (auth bypass, CPU device, Whisper-turbo
as the only loaded backend). Override with `CONFIG_PATH=...`.

## Demo

```bash
cd demo && python3 -m http.server 8080
# open http://127.0.0.1:8080
```

The demo captures your microphone, sends it through the WS, and renders
interim + final results. Use the language dropdown to test routing,
including `auto` and `hi-en` (Hinglish).

## Tests

```bash
poetry run pytest tests/unit tests/integration
# Browser-driven E2E (requires Playwright + running server)
cd tests/e2e && npm install && npx playwright test
```

## Production

```bash
CONFIG_PATH=config/prod.yaml \
  uvicorn casual_sst.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Prod expects: JWT auth, CUDA-capable device, all backends loaded.
See [docs/ARCHITECTURE.md#failure-modes--how-we-handle-them](docs/ARCHITECTURE.md).

## License

Apache 2.0.
