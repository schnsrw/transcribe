# Architecture Decision Records

Every non-trivial design decision lives here. Append, do not edit-in-place.
Each ADR is stamped with a status; superseded ADRs are kept for context.

---

## ADR-001 — Reuse Skynet's Jigasi wire format verbatim

**Status:** Accepted (2026-05-30)

**Context.** Jigasi already speaks to Skynet's `streaming_whisper` module
using a 60-byte ASCII header + raw 16 kHz mono s16le body + JSON
responses. Changing the wire format means a Jigasi-side patch.

**Decision.** Keep the byte layout, accept Jigasi traffic unchanged.
Allow opt-in extension via new header `lang` values (`auto`, `hi-en`,
`hinglish`) and new response `type` values (`language_change`); existing
clients ignore unknown enums.

**Consequences.**
- Easy migration path — point Jigasi at the new host, done.
- We inherit the 60-byte ASCII header constraint (no UUID-as-bytes etc.).

---

## ADR-002 — Split backends into NativeStreamingASR vs ChunkedASR

**Status:** Accepted (2026-05-30)

**Context.** Voxtral Realtime and Parakeet-TDT emit incrementally as
audio arrives. Whisper variants take a buffer and return a finished
transcript. Forcing both into one interface either makes Whisper require
async iteration (awkward) or makes streaming models pretend they're
chunked (wastes their latency advantage).

**Decision.** Two protocols:

```python
class NativeStreamingASR(Protocol):
    async def open_stream(self, language): ...
    async def feed(self, h, pcm) -> AsyncIterator[ASRResult]: ...
    async def force_final(self, h) -> ASRResult | None: ...
    async def close(self, h): ...

class ChunkedASR(Protocol):
    async def transcribe(self, pcm, language, initial_prompt) -> ASRResult: ...
```

`ParticipantState` picks the right code path based on `binding.kind`
from the router. Cut-mark logic applies only to chunked backends.

**Consequences.**
- Adding a backend is a single-file change in `backends/` + a YAML entry.
- The pipeline branches in `participant.py` once (chunked vs native);
  beyond that, both look the same to the rest of the code.

---

## ADR-003 — Whisper-turbo, not large-v3, as the chunked fallback

**Status:** Accepted (2026-05-30)

**Context.** large-v3 has lower hallucination rate than turbo on noisy
audio, but turbo is 6× faster and the only realistic option on a single
GPU at production traffic. Public reports (Whisper GitHub issue 2280;
arxiv 2505.12969) document turbo's elevated hallucination rate on short
and silent clips.

**Decision.** Use `large-v3-turbo` but pair it with the full hardening
stack documented in `docs/HALLUCINATION_GUARDS.md` — raised
`no_speech_threshold`, lower `log_prob_threshold`, hallucination deny-list,
post-decode probability gate, and a forced split at 8 seconds (vs
Skynet's 10).

**Consequences.**
- Higher throughput per GPU at acceptable quality.
- Maintenance cost: the deny-list grows over time. That's fine; it's data
  not code, and lives in `config/*.yaml`.

**Open question.** If on-prem GPU budget allows two models, route noisy
calls to large-v3 and clean calls to turbo via SNR detection.
Not yet implemented.

---

## ADR-004 — Background LID via faster-whisper-tiny

**Status:** Accepted (2026-05-30)

**Context.** Jigasi sends a static `lang` per participant from Jitsi UI
preference. Mid-call language changes (`en → ta`) and code-switching
(Hinglish) are common in real calls. We need to detect the actual
language without changing Jigasi.

**Decision.** Per-participant background `LIDWorker` that runs
faster-whisper-tiny's `detect_language` on a rolling 5-second window
every 3 seconds of accumulated *speech* (wall-clock-independent). Push
detections into `LangState`; require 2 consecutive disagreements
above `switch_threshold=0.85` before swapping the active backend.

**Considered and rejected.**
- Continuous LID with no hysteresis → flapping when both langs are close.
- LID only on backend-confidence drop → too slow to catch a switch
  (6-10 s vs 3-6 s).
- Per-chunk `language=None` to Whisper → script flapping mid-sentence,
  no use of incremental confidence.

**Consequences.**
- ~30 ms of CPU per detection per participant. Acceptable.
- Tiny model is multilingual but limited; some Indic langs are weak.
  For those, the participant's static header lang ends up being correct
  more often than LID. The profile-based sticky lock handles this.

---

## ADR-005 — Voxtral covers code-switching for languages it supports; AI4Bharat handles the rest

**Status:** Accepted (2026-05-30)

**Context.** Voxtral Mini Realtime supports 13 languages including Hindi,
Mandarin, Arabic. With `language=None` it handles En+Hi (Hinglish) mixes
natively. It does NOT cover Tamil, Bengali, Marathi, Telugu, Punjabi,
Gujarati, Kannada, Malayalam. Code-switched variants (Tanglish,
Banglish, etc.) need a model trained on those mixes.

**Decision.** Route Indic-only and Indic-English code-switching to
AI4Bharat IndicConformer (which has explicit code-switch support and
can emit Romanized output on request). Voxtral handles Hinglish (Hi+En)
because it's in its 13-lang set.

**Considered and rejected.**
- Hosted-only Indic (ElevenLabs Scribe v2 Realtime) — fine for v2 if
  AI4Bharat self-host is too heavy.
- Whisper-large-v3 with `language=None` for everything — script
  flapping mid-sentence is unacceptable for Indic output.

**Consequences.**
- Four backends instead of three. Worth it for full Indic coverage.
- Operationally, the GPU box needs to hold Voxtral (10 GB) + Parakeet
  (2 GB) + IndicConformer (~2 GB). Roughly 16 GB total, within an L4 /
  A10 budget.

---

## ADR-006 — Per-call participant profile, not persisted

**Status:** Accepted (2026-05-30)

**Context.** Skynet keeps a meeting-wide rolling `previous_transcription_tokens`
fed as Whisper's `initial_prompt`. That works when a meeting has one
dominant speaker but pollutes context for everyone else. We also want a
tiebreaker for LID and a default lang on reconnect.

**Decision.** `ParticipantProfile` per `(meeting, participant_id)`, in
memory only. Tracks `last_finalized_lang`, lang distribution, recent
finals, rolling confidence. Replaces Skynet's meeting-wide prompt with a
per-participant one.

**Considered and rejected.**
- Persisting profiles across calls (Redis) — privacy risk, scope creep,
  unclear win.

**Consequences.**
- Each participant has its own initial_prompt — cleaner, less cross-talk.
- Lost at WS close. Fine for a transcription service; not fine for a
  CRM. Re-evaluate if requirements change.

---

## ADR-007 — Short-utterance finalization gated by speech time, not buffer length

**Status:** Accepted (2026-05-30)

**Context.** A participant says "okay" (300 ms) then goes quiet for 5 s.
Skynet's flush worker eventually grabs the buffer, but the buffer is
mostly silence with 300 ms of speech buried in it. Whisper-turbo
hallucinates badly on near-silent buffers.

**Decision.** When the idle-flush timer fires, gate the finalize by
*detected speech ms* (via Silero VAD on the buffer), not buffer length.
Require ≥ `min_speech_ms` (default 250 ms) of actual speech, otherwise
drop the buffer as noise. Short-flush timer is 500 ms (vs 2000 ms for
the long-flush), so single-word answers finalize quickly.

**Consequences.**
- "Yes", "no", "okay" land as quick finals (~750 ms after they end).
- Coughs and lip smacks are dropped.

---

## ADR-008 — Two backend families, one router, no fallback chain

**Status:** Accepted (2026-05-30)

**Context.** Tempting to build a fallback chain: try Voxtral, on error
fall back to Whisper. Sounds robust; in practice the wrong-backend
output is silently emitted and quality drops with no signal.

**Decision.** Router maps language → exactly one backend. Backend errors
drop the chunk and log; they do not fall back to a different model.
Operational alerts catch persistent failures.

**Consequences.**
- Quality is predictable per language.
- A bad backend deploy means transcription stops for affected langs,
  loudly. Better than silent quality regression.

---

## ADR-009 — Idle-flush must NOT reset the working buffer

**Status:** Accepted (2026-05-31, replaces earlier "long-idle hard-reset")

**Context.** An earlier version of `meeting.flush_idle` did
`state._reset_buffer()` whenever `idle > long_flush_ms`. The intent was
"if a participant has been silent for >2 s and we couldn't flush, drop
the stale buffer". In practice this race-fired against any chunked
transcribe that took longer than 2 s — and on Mac M4 CPU, almost every
transcribe does. The buffer was wiped before the in-flight transcribe
finished reading it, then `_emit_from_chunked` happily ran cut-mark
math against an emptied buffer and emitted partial / wrong text.

Real user symptom: "I spoke for one minute, the transcript was empty
until I muted." Force-flush only kicked in after the audio stream
stopped because every flush_idle iteration before that was zeroing the
buffer.

**Decision.** Remove the long-idle reset from `meeting.flush_idle`
entirely. `force_short_flush` already resets the buffer on the paths
that emit a final OR drop a sub-min_speech utterance — the separate
hard-reset is both redundant and unsafe.

**Consequences.**
- A buffer that genuinely cannot be flushed (e.g. all silence) will
  sit until `force_short_flush` decides it's not worth transcribing,
  which is still O(seconds). Acceptable.
- The unit/integration tests already covered force_short_flush's
  reset paths — no test changed; the bug was strictly in
  `meeting.flush_idle`.

---

## ADR-010 — Deny-list short tokens require exact-text match

**Status:** Accepted (2026-05-31)

**Context.** The hallucination deny-list is configured per-language
plus a `"*"` wildcard. Skynet's port substring-matched every entry:
`if b in text: drop`. For phrases ("thank you for watching") that's
fine — they only appear when Whisper hallucinates the YouTube outro.
For short tokens it's catastrophic:

- `"you"` (a known Whisper silence hallucination) matched ANY word
  containing `you` — `your`, `young`, `yours`, `youth`.
- `"..."` (meant to catch ". . ." silence loops) matched ANY
  partial Whisper transcription that ended with an ellipsis like
  `"I'd like to test how..."` — i.e. effectively all interims.

Real user symptom: the interim panel was empty for the entire
monologue, because every interim ended with `"..."`.

**Decision.** Two-mode matching in `filters._matches_ban`:
- **Multi-word phrase** (contains a space) → substring match anywhere.
- **Single token** (no space) → match only if the whole text equals
  the token.

That keeps catching the actual Whisper failure modes (a bare `you`,
a bare `...`) without eating legitimate transcripts.

**Consequences.**
- 3 new regression tests in `tests/unit/test_filters.py` guard against
  re-introduction.
- If we later discover a *short* token that needs substring matching,
  the right fix is to add a more specific multi-word phrase, not to
  loosen the rule.

---

## ADR-011 — Drop the cross-boundary buffer on language switch

**Status:** Accepted (2026-05-31)

**Context.** When LID flips `LangState.active_lang` mid-call (EN → HI),
the participant's working buffer contains audio that straddles the
language boundary. The original `_handle_switch_transition` called
`force_short_flush()` BEFORE the mode flipped back to LOCKED, which
ran `_force_final_chunked` under the NEW language — i.e. it tried to
transcribe the pre-switch English audio with `lang=hi`. Whisper
produced phonetic gibberish.

Real user symptom: "I was speaking English then shifted to Hindi and
after something to English. All transcript was gibberish, not remotely
close to what I was speaking."

**Decision.** On a switch, drop the buffer entirely:

```python
async def _handle_switch_transition(self):
    await self.close()           # release native stream handle if any
    self._reset_buffer()         # drop the cross-boundary audio
    self.lang_state.mode = RouteMode.LOCKED
    return [language_change_event]
```

We lose 0-2 s of audio at the switch point. Better than emitting
confidently-wrong text.

**Considered and rejected.**
- Snapshot the OLD lang before the switch, drain with it, then flip.
  Adds state, doesn't recover much because Whisper on the cross-
  boundary audio is already lower quality.
- Re-route per chunk via lang=None auto-detect. We do that anyway in
  MULTILINGUAL mode (which is now the demo default — see ADR-012).

**Consequences.**
- Mid-call lang switches lose ≤2 s of audio at the boundary.
- Code-switching speakers should use the `auto` virtual language code
  rather than rely on LID-driven switches.

---

## ADR-012 — Demo defaults to `lang=auto` for multilingual

**Status:** Accepted (2026-05-31)

**Context.** Originally the browser demo defaulted to `lang=en`. That
puts `LangState` into LOCKED mode, so:
- LID switching CAN fire, but with a 6-8 s lag (window + hysteresis).
- During the lag, the wrong language hint is sent to Whisper.
- See [[ADR-011]] for the gibberish that produces.

Users who actually wanted "I might speak more than one language" had
no clean path — `auto` was in the dropdown but not the default.

**Decision.** Demo dropdown defaults to `lang=auto`. That maps to
`LangState.from_header("auto")` → `RouteMode.MULTILINGUAL`, which
sends `language=None` to the model on every chunk. Whisper
auto-detects per chunk, no LID is consulted, no buffer-drop transition
is needed.

**Consequences.**
- Code-switching just works out of the box.
- Pure-monolingual speakers see slightly worse first-chunk results
  because Whisper has to auto-detect once before settling.
  Acceptable trade-off; users who know their language can pick it
  explicitly from the dropdown.
