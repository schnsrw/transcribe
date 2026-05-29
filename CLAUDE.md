# CLAUDE.md — Casual-SST

This file is loaded automatically by Claude Code when working in this repo.
It tells future sessions the goals, invariants, conventions, and pitfalls.

## What this repo is

Casual-SST is a lightweight, multilingual, low-hallucination live-call
transcription service. It is the spiritual successor to the
`streaming_whisper` module in the parent `skynet` repo, but with:

1. **Pluggable ASR backends, routed by language** (Voxtral, Parakeet,
   AI4Bharat IndicConformer, faster-whisper-turbo).
2. **Jigasi-compatible wire format** kept byte-for-byte so the existing
   Jigasi config keeps working.
3. **First-class code-switching support** (Hinglish, Tanglish, Banglish,
   and `auto`).
4. **Hardened hallucination guards** for the Whisper-turbo fallback path.

The non-transcription Skynet modules (summaries, RAG, vox) are intentionally
**not** present here.

## Repo layout

```
Casual-SST/
├── CLAUDE.md                # this file
├── README.md                # quick start, points at docs
├── pyproject.toml
├── config/
│   ├── base.yaml            # shared defaults
│   ├── local.yaml           # dev, CPU, auth bypass — CONFIG_PATH default
│   └── prod.yaml            # GPU, JWT, full models
├── docs/
│   ├── ARCHITECTURE.md      # system overview + diagrams + patterns
│   ├── CODEGRAPH.md         # module map + dependency graph
│   ├── DECISIONS.md         # ADRs
│   ├── PROTOCOL.md          # WS wire format spec
│   └── HALLUCINATION_GUARDS.md   # Whisper-turbo hardening stack
├── demo/
│   └── index.html           # browser demo for local testing
├── src/casual_sst/
│   ├── main.py              # FastAPI app + WS endpoint
│   ├── meeting.py           # MeetingConnection (per-WS)
│   ├── participant.py       # ParticipantState (per participant_id)
│   ├── lang_state.py        # language-state machine
│   ├── profile.py           # per-call participant profile
│   ├── lid.py               # background language ID worker
│   ├── vad.py               # Silero VAD wrapper
│   ├── cut_mark.py          # final/interim split for chunked backends
│   ├── filters.py           # hallucination denylist + thresholds
│   ├── frame.py             # 60-byte header parser
│   ├── router.py            # config-driven backend router
│   ├── types.py             # protocols, dataclasses
│   └── backends/
│       ├── base.py
│       ├── whisper_turbo.py     # ChunkedASR — fully implemented
│       ├── voxtral.py           # NativeStreamingASR — stub
│       ├── parakeet.py          # NativeStreamingASR — stub
│       └── indic_conformer.py   # NativeStreamingASR — stub
└── tests/
    ├── unit/                # pytest, deterministic modules
    ├── integration/         # pytest, mock backends, full pipeline
    └── e2e/                 # Playwright, demo.html → live server
```

## Invariants — do not break these

1. **Wire format is frozen** at the Jigasi-compatible layout described in
   `docs/PROTOCOL.md`. Adding new response fields is allowed; removing or
   renaming existing ones is not.
2. **`condition_on_previous_text` is hard-coded to `False`** in every
   Whisper-family backend. This is the single biggest hallucination guard.
   Config can not flip it to `True`.
3. **All chunked backends go through `cut_mark.find()` + `filters.is_hallucination()`**.
   Bypassing either is how Skynet's hallucinations leak back in.
4. **Backend interfaces** (`NativeStreamingASR`, `ChunkedASR`) are
   semantic contracts — adding a backend means implementing one of them,
   not adding pipeline branches in `participant.py`.
5. **Language state is per-participant**, not per-chunk. The Jigasi
   header `lang` is a *hint*, not truth; the LID worker can override it.

## Conventions

- Python ≥ 3.11.
- Module + class docstrings on every file. Public functions get a one-line
  docstring. Non-obvious logic gets WHY comments referencing an ADR (e.g.
  `# see DECISIONS.md ADR-005`).
- Config-driven: every threshold, model name, or behavior toggle lives in
  `config/*.yaml`. No magic constants in code (except a few clearly-named
  protocol constants like `HEADER_BYTES = 60`).
- Tests: every PR adds/updates unit tests for changed deterministic
  modules. Integration tests cover the participant pipeline.

## Default workflow when asked to change behavior

1. Identify which module owns the behavior (CODEGRAPH.md helps).
2. If the change is a new design decision, add an ADR to
   `docs/DECISIONS.md` *before* the code change.
3. Update the relevant unit/integration test first.
4. Make the code change.
5. Re-run tests.
6. Update CLAUDE.md only if a new invariant was introduced.

## What NOT to do

- Don't add a new backend by branching inside `participant.py`. Add a
  file in `backends/`, register it in config, route to it.
- Don't sneak in `condition_on_previous_text=True` "just for an experiment".
- Don't widen the wire format response without first updating the demo
  and the protocol doc.
- Don't add a comment that describes WHAT the code does. Describe WHY,
  reference the ADR, or delete it.
