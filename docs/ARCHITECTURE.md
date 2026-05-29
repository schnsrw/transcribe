# Architecture

## Goal

Live, multilingual, low-hallucination transcription of telephony / WebRTC
audio coming from Jigasi (or any client that speaks the same wire format).
Sub-second latency on speech-final events. Self-host first, hosted-API
fallback later if needed.

## High-level diagram

```
                          ┌──────────────────────────────────────┐
                          │             Jigasi  /  Demo          │
                          │  (audio capture, 16 kHz s16le mono)  │
                          └───────────────┬──────────────────────┘
                                          │ WebSocket
                                          │ binary frames:
                                          │   [60B header  pid|lang]
                                          │   [PCM payload]
                                          ▼
                          ┌──────────────────────────────────────┐
                          │  main.py  /ws/{meeting_id}           │
                          │  FastAPI WebSocket endpoint          │
                          └───────────────┬──────────────────────┘
                                          │
                                          ▼
                          ┌──────────────────────────────────────┐
                          │  MeetingConnection (meeting.py)      │
                          │   - participants: dict[pid → State]  │
                          │   - idle-flush sweep (1Hz)           │
                          └───────────────┬──────────────────────┘
                                          │ frame.parse()
                                          ▼
              ┌───────────────────────────────────────────────────────┐
              │  ParticipantState  (participant.py)                   │
              │                                                       │
              │  ┌──────────┐   ┌────────────┐   ┌─────────────────┐  │
              │  │ Silero   │   │ LangState  │   │ Participant     │  │
              │  │  VAD     │   │ machine    │   │ Profile         │  │
              │  └────┬─────┘   └─────┬──────┘   └────────┬────────┘  │
              │       │               │                    │          │
              │       └────────┬──────┴──────┬─────────────┘          │
              │                ▼             ▼                        │
              │       ┌────────────────────────────┐                  │
              │       │  Background LIDWorker       │  faster-whisper │
              │       │  (every 3s of speech,       │  -tiny detect   │
              │       │   window 5s)                │                 │
              │       └────────────────────────────┘                  │
              │                       │                               │
              │                       ▼                               │
              │       ┌────────────────────────────┐                  │
              │       │  Router.for_language(lang) │                  │
              │       └─────────┬─────────┬────────┘                  │
              │                 │         │                           │
              │       ┌─────────▼──┐   ┌──▼──────────────┐            │
              │       │ Chunked    │   │ NativeStreaming │            │
              │       │ pipeline   │   │ pipeline        │            │
              │       │ (VAD →     │   │ (open → feed →  │            │
              │       │  buffer →  │   │  consume stream)│            │
              │       │  call →    │   │                 │            │
              │       │  cut-mark) │   │                 │            │
              │       └────┬───────┘   └────────┬────────┘            │
              │            │                    │                     │
              │            ▼                    ▼                     │
              │   ┌────────────────┐   ┌──────────────────┐           │
              │   │ filters.       │   │ filters.         │           │
              │   │  is_hallucin.  │   │  is_hallucin.    │           │
              │   └────────┬───────┘   └────────┬─────────┘           │
              │            └────────┬───────────┘                     │
              │                     ▼                                 │
              │            ┌──────────────────┐                       │
              │            │ TranscriptionEv. │                       │
              │            │ → ws.send_json   │                       │
              │            └──────────────────┘                       │
              └───────────────────────────────────────────────────────┘
```

## Component responsibilities

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app, WS endpoint, idle-flush background task |
| `meeting.py` | One per WebSocket. Holds participant map. Fans out frames. |
| `participant.py` | Heart of the pipeline. One per `(meeting, participant_id)`. Owns buffer, VAD, LID, lang state, profile. Delegates to a backend. |
| `lang_state.py` | LangState machine (LOCKED → SWITCHED → MULTILINGUAL). Hysteresis + sticky-lock. |
| `profile.py` | Per-call ParticipantProfile. Tiebreaker for LID, source of personalized initial_prompt. |
| `lid.py` | Background language-ID worker. faster-whisper-tiny on a 5s window every 3s of speech. |
| `vad.py` | Silero VAD wrapper (speech timestamps, total speech time, silence detection). |
| `cut_mark.py` | Chunked-backend-only. Splits Whisper output into final/interim at punctuated boundaries or biggest inter-word gap. |
| `filters.py` | Hallucination denylist + min-probability gate + repetition detector. |
| `frame.py` | Jigasi 60-byte header parse + audio-time helpers. |
| `router.py` | Config-driven backend instantiation + per-language lookup. |
| `types.py` | `NativeStreamingASR` / `ChunkedASR` protocols, `ASRResult`, `Word`, `TranscriptionEvent`. |
| `backends/*` | Each ASR backend. One file = one backend = one config entry. |

## Design patterns in use

| Pattern | Where | Why |
|---|---|---|
| **Strategy** | `backends/*` behind `ChunkedASR` / `NativeStreamingASR` protocols | Swap models without touching the pipeline. |
| **Configuration over code** | `config/*.yaml` drives routing, thresholds, model names | Tune live behavior without redeploys; same code runs in local + prod. |
| **State machine** | `LangState` (LOCKED / SWITCHED / MULTILINGUAL) | Code-switching and mid-call language changes are explicit, testable. |
| **Producer/consumer** | `LIDWorker.feed()` + `maybe_run()` background | LID never blocks the audio path; runs in its own asyncio task / threadpool. |
| **Plugin registry** | `Router.load_backends()` instantiates backends by dotted-path string | Adding a backend is YAML + one file. |
| **Decorator-ish guards** | `filters.is_hallucination` called at every emit | Centralized hallucination policy, applied uniformly. |
| **Object pool (per-participant)** | One backend stream handle per native-streaming participant, recycled across chunks | Avoid model load per chunk. |

## Data flow — chunked (Whisper-turbo) path

```
audio chunk
    │
    ▼
ParticipantState.push
    │
    ├── Silero VAD on (working_audio + chunk)
    │       │
    │       ▼
    │   if speech_changed:  append to buffer, update last_speech_end
    │   else:               increment silent counter; mark long_silence if >1s
    │
    ├── LIDWorker.feed(chunk, new_speech_s)
    │   LIDWorker.maybe_run() → on detection, push to LangState.observe
    │
    ├── if lang_state.mode == SWITCHED → flush old backend, emit language_change
    │
    └── route(active_lang) → ChunkedBackend.transcribe(buffer, lang, prompt)
            │
            ▼
        cut_mark.find(words)
            │
            ├── if final part → trim buffer, emit final event
            └── if interim part → emit interim event
```

## Data flow — native streaming (Voxtral/Parakeet/IndicConformer) path

```
audio chunk
    │
    ▼
ParticipantState.push
    │
    ├── Silero VAD (only used for LID feeding + idle detection)
    ├── LIDWorker.feed(chunk, new_speech_s)
    └── route(active_lang) → NativeStreamingBackend.feed(handle, chunk)
            │
            ▼
        async iterator of ASRResult
            │
            └── for each: filter, emit event (interim/final per backend's signal)
```

Cut-mark logic is **not** applied to native-streaming results — the model
owns finalization. See `DECISIONS.md` ADR-002.

## Why two backend families instead of one

We could force every backend behind a single `transcribe(buffer)` contract,
but it would throw away the latency advantage of natively-streaming
models. Voxtral and Parakeet emit interims *during* a chunk; forcing them
to return only at chunk boundary would add ≥ 1s of unnecessary latency.

The cost is one extra abstraction (`NativeStreamingASR`), which is worth
it.

## Concurrency model

- Single asyncio event loop in FastAPI/uvicorn.
- One coroutine per WebSocket (the handler in `main.py`).
- One background coroutine per meeting for idle flushing (driven by
  `MeetingConnection.flush_idle`).
- One global `_flush_loop` runs at 1 Hz and walks all meetings.
- ASR backends run their CPU/GPU work in `asyncio.to_thread` /
  `run_in_executor`. The event loop stays responsive.
- LID runs in a threadpool, gated by an `inflight` flag so only one LID
  job per participant is in the air at a time.

## Failure modes & how we handle them

| Failure | Mitigation |
|---|---|
| Backend OOM / unavailable on startup | `router.load_backends()` raises; container fails fast and is restarted. No silent fallback. |
| Backend crash mid-call | Caught in `participant._call_chunked`; the chunk is dropped, buffer is preserved, next chunk retries. |
| LID flapping between langs | Hysteresis (2 consecutive agreements) + `LangState` sticky lock once profile has 30+ finals. |
| Short utterances (cough-then-quiet) | `min_speech_ms` floor (250 ms) drops them silently. |
| Whisper hallucination on silence | Stack in `docs/HALLUCINATION_GUARDS.md`. |
| Client disconnect mid-transcribe | `WebSocketDisconnect` caught; backend handles are closed; buffer GC'd. |

## What we explicitly didn't build

- Summarization / LLM post-processing (parent skynet does this).
- Speaker diarization (Jigasi gives us `participant_id` per chunk).
- Audio re-encoding (we assume 16 kHz s16le mono per protocol).
- Persistence (transcripts are emitted to the WS client and forgotten).
- Multi-tenancy / billing (out of scope; the WS auth_token + meeting_id
  are the only access controls).
