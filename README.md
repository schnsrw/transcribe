# Casual-SST

Lightweight, multilingual, low-hallucination live-call transcription.
Jigasi-compatible WebSocket wire format. Pluggable ASR backends routed
by language.

## At a glance

| | |
|---|---|
| Wire protocol | Jigasi-compatible — see [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| Backends | mlx-whisper (Mac dev) · faster-whisper (Linux + prod) · Voxtral · Parakeet · AI4Bharat IndicConformer (stubs) |
| Languages | 13 native + 25 EU + 8 Indic + 99 fallback |
| Code-switching | Hinglish · Tanglish · Banglish · `auto` |
| Hallucination guards | See [docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md) |
| Configs | `base.yaml` + `local.yaml` (Docker dev) / `dev-mac.yaml` / `dev-linux.yaml` / `prod.yaml` |

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

**Measured perf** (M4 base, whisper-large-v3-turbo-q4, 5 s audio,
warm model): WHOLE-FILE ≈ 2.8 s first event, STREAM-1s-paced ≈ 24 s.
Mac mode is faster for batch / WHOLE-FILE iteration; streaming on
M4 base has per-call MLX overhead that doesn't amortize, so for
real Jigasi-style 1 s-chunk simulation use `make dev-docker`. See
[docs/DECISIONS.md#adr-013](docs/DECISIONS.md) for measurements +
rationale.

## Quickstart with Docker (works everywhere)

```bash
make dev-docker     # API on :8000, demo on :8080
make stop           # tear down, keep the model cache
```

The first build downloads whisper-small (~500 MB) into the
`casual-sst-hf-cache` named volume.

## Docs

- **[CLAUDE.md](CLAUDE.md)** — orientation, invariants, conventions
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system overview + diagrams
- **[docs/CODEGRAPH.md](docs/CODEGRAPH.md)** — module map + dependency graph
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — ADRs, including ADR-013 (dual-runtime decision)
- **[docs/PROTOCOL.md](docs/PROTOCOL.md)** — wire format spec
- **[docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md)** — Whisper hardening stack
- **[tests/live/README.md](tests/live/README.md)** — live test harness

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

## License

Apache 2.0.
