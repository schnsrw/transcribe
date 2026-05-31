# Contributing to Casual-SST

Thanks for being interested. The repo is small and opinionated;
contributions are easiest when they fit the patterns described in
`CLAUDE.md` and `docs/ARCHITECTURE.md`. Read those first.

## Quick orientation

| If you want to … | Start at |
|---|---|
| Run the service locally | [docs/SETUP.md](docs/SETUP.md) |
| Understand the architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Add a new ASR backend | [docs/DECISIONS.md](docs/DECISIONS.md) ADR-002 + `backends/base.py` |
| Change something risky | [CLAUDE.md](CLAUDE.md) — invariants list |

## Development loop

Pick the run mode that matches your machine; full instructions in
`docs/SETUP.md`.

```bash
make dev-mac        # Apple Silicon, host venv with MLX
make dev-linux      # Linux host (CUDA if available, else CPU)
make dev-docker     # cross-platform, CPU only, slow on Mac
make test           # unit + integration tests in Docker
```

## What "ready to merge" looks like

- [ ] `make test` is green locally (55+ tests).
- [ ] If you touched the transcription pipeline, you also added at least
  one regression test in `tests/unit/` or `tests/integration/`.
- [ ] If you introduced a non-trivial design decision, you added an ADR
  to `docs/DECISIONS.md` (next free `ADR-NNN`). Append, do not edit
  past entries.
- [ ] If you added a new public API endpoint, the OpenAPI docs at
  `/docs` reflect it (FastAPI generates that automatically; just
  verify by hitting `http://localhost:8100/docs`).
- [ ] If you added a config knob, it's documented in `config/base.yaml`
  with a comment and listed in `.env.example` if env-overridable.
- [ ] CI on the PR passes.

## Conventions

- Python 3.11+. We use `from __future__ import annotations` everywhere.
- Module + class docstrings on every file. Public functions get a
  one-liner; non-obvious logic gets a WHY-comment that references an
  ADR.
- No magic constants. New thresholds go in `config/*.yaml`.
- Tests under `tests/unit/` (deterministic, no I/O) and
  `tests/integration/` (mock backends, full pipeline). The live
  harness in `tests/live/` is for manual / nightly testing, not CI.

## Invariants — please don't break

Cross-reference `CLAUDE.md`. Highlights:

1. **Wire format is frozen.** Adding new response fields is fine;
   removing or renaming existing ones requires a protocol-version bump.
2. **`condition_on_previous_text` is hard-coded `False`** in every
   Whisper-family backend.
3. **Chunked backends always go through `cut_mark.find` +
   `filters.is_hallucination`.**
4. **`meeting.flush_idle` must NOT call `state._reset_buffer()`**
   (ADR-009 — race against in-flight transcribes).
5. **Deny-list short tokens (no space) match exact text only** (ADR-010).
6. **`_handle_switch_transition` drops the buffer; it does NOT drain
   it under the new language hint** (ADR-011).

## Reporting bugs / asking questions

Issues on the GitHub repo are fine. Please include:

- Run mode (Mac dev / Linux dev / Docker dev / prod) + OS.
- The exact `make ...` command you ran.
- Server log (last 50 lines from `/admin/api/logs` if the portal is
  on, otherwise from container stdout).
- Audio sample if the bug is transcription-specific (16 kHz mono WAV
  preferred).

## License

By contributing you agree your contributions are licensed under
Apache 2.0 (the same as the rest of the project).
