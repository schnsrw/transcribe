# Code graph

Module dependency graph. Arrows go from importer → importee. Cycles are
errors (and there are none — verified by `tests/unit/test_import_graph.py`).

```
                       main.py
                          │
                          ├──► meeting.py
                          │       │
                          │       ├──► participant.py
                          │       │       │
                          │       │       ├──► vad.py
                          │       │       ├──► cut_mark.py ─► types.py
                          │       │       ├──► filters.py  ─► types.py
                          │       │       ├──► lang_state.py
                          │       │       │       └──► profile.py ─► types.py
                          │       │       ├──► lid.py
                          │       │       ├──► profile.py
                          │       │       ├──► router.py
                          │       │       │       │
                          │       │       │       ├──► types.py
                          │       │       │       └──► backends/*.py
                          │       │       │               │
                          │       │       │               ├──► backends/base.py
                          │       │       │               └──► types.py
                          │       │       │
                          │       │       └──► frame.py
                          │       │
                          │       └──► frame.py
                          │
                          └──► router.py
```

## Per-module summary

### `main.py`
- FastAPI app and `/ws/{meeting_id}` handler.
- Owns the global meetings registry `_meetings: dict[str, MeetingConnection]`.
- Spawns the 1 Hz `_flush_loop` background task.
- Imports: `meeting`, `router`, `frame`.

### `meeting.py`
- `MeetingConnection` — one per WebSocket.
- Public: `handle_frame(bytes)`, `flush_idle()`, `close()`.
- Imports: `frame`, `participant`, `router`, `types`.

### `participant.py`
- `ParticipantState` — the integration point. One per
  `(meeting_id, participant_id)`.
- Public: `push(pcm)`, `force_short_flush()`, `close()`.
- Internal: chunked path (`_push_chunked` / `_force_final_chunked`)
  vs native path (`_push_native` / `_force_final_native`).
- Imports: `vad`, `cut_mark`, `filters`, `frame`, `lang_state`,
  `lid`, `profile`, `router`, `types`.

### `lang_state.py`
- `LangState` state machine: `LOCKED` ↔ `SWITCHED` → `LOCKED`,
  plus `MULTILINGUAL` (terminal once chosen).
- Public: `from_header(header_lang)`, `observe(lang, conf, profile, now_ms)`.
- Imports: `profile`.

### `profile.py`
- `ParticipantProfile` — per-call stats and tiebreaker source.
- Public: `record_final(result, lang, ms)`, `personalized_initial_prompt()`,
  `dominant_lang()`, `locked_strong` (property).
- Imports: `types`.

### `lid.py`
- `LIDWorker` — async producer/consumer that runs faster-whisper-tiny
  every N seconds of accumulated speech.
- Public: `feed(pcm, speech_s)`, `should_run()`, `maybe_run()`.
- Standalone helper: `detect_sync(pcm, device)`.
- Imports: `frame` (sample rate).

### `vad.py`
- Pure-function wrappers over Silero VAD.
- Public: `speech_timestamps(audio)`, `total_speech_ms(audio)`,
  `is_silent(audio)`.
- Imports: `frame` (sample rate constants).

### `cut_mark.py`
- Splits a `WhisperResult.words` list into `(final, interim)` at a
  high-confidence punctuated boundary, or biggest inter-word gap when
  the audio is runaway-long.
- Public: `find(words, …)`, `split(words, mark)`.
- Imports: `types` (`Word`).

### `filters.py`
- `is_hallucination(result, lang, denylist, min_prob)` — central drop policy.
- `in_prompt_blacklist(text, blacklist)` — used by `_record_final` to
  skip seeding bad finals into the prompt.
- Imports: `types`.

### `frame.py`
- 60-byte header parse + audio<->seconds<->bytes helpers.
- Public: `parse(buf)`, `bytes_to_seconds()`, `seconds_to_bytes()`,
  constants `HEADER_BYTES`, `DISCONNECT_BYTE`, `SAMPLE_RATE`.
- Imports: stdlib only.

### `router.py`
- `Router` — config-driven backend instantiation and per-language lookup.
- `load_config(path)` reads YAML + merges base + env-specific overrides.
- Imports: `types`, `backends/*`.

### `types.py`
- `Word`, `ASRResult`, `TranscriptionEvent`.
- `NativeStreamingASR`, `ChunkedASR` protocols (typing.Protocol).
- No imports outside stdlib.

### `backends/base.py`
- `BackendBase`, `NativeStreamingBackend` (ABC), `ChunkedBackend` (ABC).
- Concrete backends inherit from one of these.

### `backends/whisper_turbo.py`
- Fully implemented. Reads `config.backends.whisper_turbo`.
- Hard-codes `condition_on_previous_text=False`.

### `backends/voxtral.py`, `backends/parakeet.py`, `backends/indic_conformer.py`
- Stubs that raise `NotImplementedError` until model loading is wired.
- Each declares its `SUPPORTED` language set as a class constant for
  validation against the routing table.

## Import-graph rules (enforced by lint + a unit test)

1. No module under `src/casual_sst/` may import from `tests/`.
2. `types.py` may import only from the standard library.
3. `backends/*` may import from `types`, `frame`, and `backends/base`.
   They may not import from `participant`, `meeting`, `main`, `router`,
   `lid`, `lang_state`, `profile`, or `vad`.
4. `participant.py` is the only file allowed to import both
   `lang_state` and `router`.
