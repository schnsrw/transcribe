# Changelog

All notable changes to Casual-SST are listed here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

(Nothing yet — the next release will roll up everything below.)

## [0.0.1] — 2026-06-01

First public release. Captures the work from the initial sprint that
extracted a focused live-transcription service from the parent
`jitsi/skynet` repo's `streaming_whisper` module.

### Added

#### Transcription core (Jigasi-compatible)

- WebSocket endpoint `/ws/{meeting_id}` with the same 60-byte ASCII
  header + raw 16 kHz s16le PCM body Jigasi already emits
  ([docs/PROTOCOL.md](docs/PROTOCOL.md), ADR-001).
- Two backend protocols: `NativeStreamingASR` and `ChunkedASR`
  (ADR-002). Pluggable per-language via the router.
- Per-participant pipeline: Silero VAD gate, cut-mark final/interim
  split, long-silence flush, rolling per-participant initial prompt,
  per-participant profile, background language ID worker.
- Multilingual code-switching support via virtual `lang` codes
  (`auto`, `hi-en`, `ta-en`, `bn-en`, `hinglish`).
- Hallucination guard stack ([docs/HALLUCINATION_GUARDS.md](docs/HALLUCINATION_GUARDS.md)):
  Whisper threshold knobs (`no_speech_threshold`,
  `compression_ratio_threshold`, `log_prob_threshold`,
  `hallucination_silence_threshold`) + post-decode deny-list with
  two-mode matching (multi-word phrase substring vs single-token
  exact text) + repetition detector.

#### Backends shipped

- `whisper_turbo` — faster-whisper (CTranslate2). Production target on
  Linux + CUDA. Local dev default in `config/local.yaml` (Docker) and
  `config/dev-linux.yaml`.
- `mlx_whisper` — mlx-whisper on Apple Metal. Mac host-mode default
  in `config/dev-mac.yaml`. Same OpenAI weights as production.
- `whisper_cpp` — pywhispercpp + Metal. Alternate Mac path
  (`config/dev-mac-cpp.yaml`) — known chunk-handoff issues on
  STREAM, documented in ADR-013.
- `voxtral`, `parakeet`, `indic_conformer` — stubs with implementation
  roadmaps in their docstrings.

#### Operations

- Admin / monitoring portal at `/admin/` with token-gated JSON API
  (`/admin/api/{status,meetings,logs,config,reset-metrics}`) — vanilla
  JS dashboard, no build step (ADR-014).
- Prometheus `/metrics` endpoint — hand-rolled exposition, no
  `prometheus_client` dep.
- Liveness `/healthz` and readiness `/health` (gates backend health,
  not just TCP-accept).
- JWT auth in `/ws/{meeting_id}?auth_token=...` when
  `bypass_auth=false` (ADR-015) — ASAP-first (Jitsi-style PEM keys)
  with HMAC fallback. PyJWT[crypto] required.
- Optional LLM summarisation: `POST /api/summarize` with OpenAI-
  format or Ollama backends (ADR-016). Off by default.

#### Infrastructure

- Slim CPU `Dockerfile` (700 MB, down from the earlier 9 GB) using
  the torch CPU wheel index.
- `Dockerfile.cuda` + `compose.prod-cuda.yaml` for Linux + NVIDIA
  production deploys.
- Four configs (`base.yaml` + `local.yaml` + `dev-mac.yaml` +
  `dev-mac-cpp.yaml` + `dev-linux.yaml` + `prod.yaml`) layered via
  `extends:` + recursive deep-merge.
- `Makefile` with `dev-mac`, `dev-mac-cpp`, `dev-linux`,
  `dev-docker`, `test`, `prod`, `prod-stop`, `prod-logs`,
  `stop`, `logs`, `clean`.
- Host-mode launchers `scripts/dev-mac.sh` + `scripts/dev-linux.sh`
  with self-contained `.venv-*` directories.

#### CI / release

- `.github/workflows/ci.yml` — pytest in the Docker test image on
  every push to `main` and every PR.
- `.github/workflows/release.yml` — on `v*` tag, runs the test gate,
  then publishes CPU image (linux/amd64 + linux/arm64) and CUDA
  image (linux/amd64) to Docker Hub with semver + `latest` tags.
  Credentials live in a GitHub `release` environment (ADR-017).

#### Tests

- 55 pytest unit + integration tests covering the transcription
  pipeline (`frame`, `cut_mark`, `filters`, `profile`, `lang_state`,
  `router`, `import_graph`, `pipeline` with mock backends).
- Live test harness (`tests/live/run_cases.py`) — 25 audio cases
  across English / Hindi / Spanish / German + negative cases for
  silence and the "Thank you for watching" hallucination trap.
- Playwright E2E scaffold (`tests/e2e/`) — drives the demo HTML
  against a live server.
- WHOLE-FILE vs STREAM head-to-head probe at
  `tests/live/probes/compare.py`.

#### Docs

- `docs/ARCHITECTURE.md`, `docs/CODEGRAPH.md`, `docs/DECISIONS.md`
  (17 ADRs), `docs/PROTOCOL.md`, `docs/HALLUCINATION_GUARDS.md`.
- `docs/SETUP.md` — end-to-end setup for Mac dev / Linux dev /
  Docker / production with prerequisites + verify + troubleshooting
  per path.
- `docs/DOCKER_GPU.md` — Docker + NVIDIA GPU on Linux walkthrough.
- `CLAUDE.md` — invariants + conventions for future Claude sessions.
- `.env.example` with every env var the service reads.

### Known limitations

- whisper.cpp `STREAM` mode drops content on chunk-handoff boundaries
  on M4 base — use `mlx_whisper` (default) for Mac streaming dev.
- Voxtral / Parakeet / IndicConformer backends are stubs.
- Mac host-mode streaming on M4 base is slow on non-English (per-call
  MLX overhead). English STREAM is real-time; Hindi STREAM is not.
  See ADR-013 measurement matrix.
- No rate limiting on `/api/summarize` — protect with a reverse proxy
  if exposed publicly.

[Unreleased]: https://github.com/schnsrw/transcribe/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/schnsrw/transcribe/releases/tag/v0.0.1
