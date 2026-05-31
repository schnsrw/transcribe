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

---

## ADR-013 — Dual-runtime: mlx-whisper for Mac dev, faster-whisper for Linux + prod

**Status:** Accepted (2026-05-31)

**Context.** Docker Desktop on macOS cannot expose Apple Metal or the
Neural Engine to Linux containers ([Docker's own announcement, 2026]
confirms they ship "Docker Model Runner" specifically because Metal
passthrough is impossible). Our existing stack — `faster-whisper`
running on Linux + CUDA in production — therefore degrades to CPU-only
on Mac dev, taking 30-60 s per chunked transcribe of Hindi long
audio. That's unusable for iteration.

We need a Mac dev path that uses the M-series GPU, while keeping the
existing Linux+CUDA prod path untouched.

**Decision.** Two backends, picked by config:

- `casual_sst.backends.whisper_turbo` — faster-whisper + CTranslate2.
  Used by `dev-docker`, `dev-linux`, and **prod**. CUDA or CPU.
- `casual_sst.backends.mlx_whisper` — `mlx-whisper` on Apple Metal.
  Used by `dev-mac` only. Lives in the optional `mac-dev` poetry
  group; never installed in the Docker image.

Both backends:
- Load the **same OpenAI Whisper weights** (large-v3-turbo by default).
- Implement the same `ChunkedASR` protocol with identical kwargs:
  `no_speech_threshold`, `compression_ratio_threshold`,
  `condition_on_previous_text=False`, `initial_prompt`, `beam_size=1`,
  `word_timestamps=True`.
- Return the same `ASRResult` shape — the pipeline above (cut-mark,
  filters, profile) doesn't know which one ran.

**Considered and rejected.**
- `whisper.cpp + pywhispercpp` (Metal). Close second — same C++ family
  Linux fallback. Rejected because mlx-whisper is ≈2× faster on M4
  large-v3-turbo per Jan 2026 benchmarks, and the DTW word-timestamp
  reimplementation in whisper.cpp has documented drift vs OpenAI.
- `openai-whisper` + `device='mps'`. Word timestamps are broken on
  MPS in current PyTorch (transformers issue #36093).
- `WhisperKit` on ANE. Swift-only — subprocess boundary kills our
  async pipeline ergonomics. Worth revisiting when there's a clean
  Python binding.
- `insanely-fast-whisper` on MPS. Semantic differences in
  `condition_on_previous_text` would diverge from prod.

**Parity caveats (must be guarded by tests).**
1. **Tokenizer identical** — same tiktoken multilingual base. Devanagari
   handling, language codes, special tokens all match.
2. **Word-timestamp precision drifts ≤80 ms** between mlx-whisper's
   Python DTW port and faster-whisper's CT2 implementation. Affects
   cut-mark split timestamps, not text content. `tests/live/probes/
   parity.py` diffs both backends on the same fixture nightly.
3. **VAD ownership stays in our pipeline.** Neither backend's internal
   VAD is enabled (`vad_filter=False` semantics for both); we run
   Silero VAD in `casual_sst.vad` upstream so the gate is identical.
4. **Kwarg name alias**: faster-whisper says `log_prob_threshold`,
   mlx-whisper says `logprob_threshold`. The MLX backend accepts both.
5. **Memory leak** with `word_timestamps=True` in mlx-examples #1254.
   Mitigated by re-instantiating every `reset_after_calls` (default
   200) calls.

**Consequences.**
- `.venv-mac/` and `.venv-linux/` are gitignored host-side venvs created
  by `scripts/dev-mac.sh` and `scripts/dev-linux.sh`. `make clean`
  wipes them. This is the "minimal host residue" carve-out from the
  no-host-residue rule.
- Two backends to maintain, but the surface is small (~100 LOC each)
  and the contract is enforced by the protocol type.
- New regression risk: forgetting to keep the two backends in sync as
  config knobs evolve. The parity probe is the safety net.

**Measured perf (M4 base, warm model, 5 s English pangram + 2 s Hindi):**

| Model | Audio / mode | First event | Total | Notes |
|---|---|---|---|---|
| large-v3-turbo-q4 | EN whole | 2.8 s | 6 s | ≈2× faster than Docker. |
| large-v3-turbo-q4 | EN stream (5 chunks) | 23 s | 45 s | Slower than Docker. |
| **whisper-small-mlx** | EN whole | **0.6 s** | 1.3 s | Best on M4 base. |
| **whisper-small-mlx** | EN stream (5 chunks) | **4.0 s** | 9.7 s | **Usable for streaming dev.** |
| whisper-small-mlx | HI whole | 0.7 s | 2.0 s | Devanagari preserved. |
| whisper-small-mlx | HI stream (2 chunks) | 21 s | 22 s | Hindi STREAM stays slow even warm. |

`whisper-small-mlx` is the default in `dev-mac.yaml` because it's the
only configuration that makes streaming usable on M4 base. Switch to
turbo-q4 if you care about WHOLE-FILE accuracy more than streaming.

**Hindi STREAM remains slow on Mac.** The per-call MLX overhead is
visibly larger on non-English audio — appears to be Hindi-decoder
token-step cost combined with mlx-whisper's word-timestamp DTW which
doesn't shrink with audio length. This is **not** model loading
(warm runs are just as slow). Confirmed by direct measurement: same
2 s Hindi WHOLE-FILE takes 0.7 s, 1 s × 2 chunks takes 21 s.

**Practical guidance:**
- `dev-mac` with small: best for English STREAM + multilingual WHOLE-FILE
  pipeline iteration.
- `dev-docker`: when you need realistic 1 s-chunk streaming in Hindi /
  other non-English languages.
- `prod`: faster-whisper on CUDA, no per-call overhead issues.
- `dev-mac-cpp` (whisper.cpp + Metal): now wired but with known
  word-boundary issues on short chunks. Keeps the model resident
  across calls so the per-call MLX overhead doesn't apply, but
  whisper.cpp's DTW reconstruction has separate accuracy quirks.

---

## ADR-014 — Admin / monitoring portal + Prometheus

**Status:** Accepted (2026-06-01)

**Context.** Skynet ships Prometheus on port 8001 plus a couple of
operator-facing scripts. Casual-SST needed equivalent operator visibility
— uptime, active meetings, recent transcripts, per-backend latency
histograms, current config — without bolting on a separate sidecar.

**Decision.** Two complementary surfaces:

1. **`/admin/*` JSON API + HTML dashboard** — interactive operator
   tool. Token-gated via the `ADMIN_TOKEN` env var (missing token →
   every route returns 404, portal effectively disabled). Endpoints
   expose status, active-meeting drill-down, log tail, resolved
   config, and a reset-counters button. Dashboard polls every 2 s.
2. **`/metrics`** — Prometheus exposition. Always on,
   unauthenticated. Counters only (no PII). Gate behind a reverse
   proxy if you don't want it public.

In-process metrics live in a single `Metrics` singleton mutated by
the meeting/participant hooks. Log capture is via an in-memory ring
buffer handler on the root logger (last 2000 lines).

**Considered and rejected.**
- `prometheus_client` library — small win, real cost (runtime image
  grew). Plain-text exposition is 30 lines.
- Real auth (OAuth) for `/admin` — out of scope for a local-ops tool.
  Put real auth on the reverse proxy.

**Consequences.**
- Operators get a usable dashboard without bringing up Grafana.
- Anyone with read-network-access to `/metrics` can see counts (not
  text). That's the right default for transcription metrics.

---

## ADR-015 — JWT auth: ASAP-first, HMAC fallback

**Status:** Accepted (2026-06-01)

**Context.** Production must enforce JWT on `/ws/{meeting_id}` —
Skynet's production deployments use Jitsi-style ASAP (asymmetric JWT
with `kid` pointing at a PEM file). Smaller deployments want HMAC.

**Decision.** `casual_sst.auth.JWTValidator` tries the two modes in
order:

1. **ASAP** (Jitsi-compatible). PEM keys discovered in
   `ASAP_PUB_KEYS_FOLDER` keyed by `kid`. Audience must appear in
   `ASAP_PUB_KEYS_AUDS`. Algorithms accepted: RS256/RS512/ES256/ES384.
2. **HMAC** fallback. `JWT_SECRET` + `JWT_ALGORITHM` (default HS256)
   + optional `JWT_AUDIENCE`.

If `bypass_auth=false` and *neither* set of env vars is configured,
the validator raises at first request — misconfigured prod fails
loudly, not silently. Dev stacks (`bypass_auth=true`, the default in
`local.yaml`/`dev-mac.yaml`/`dev-linux.yaml`) never instantiate the
validator and never need PyJWT-the-import to even succeed.

**Consequences.**
- One-line drop-in for Jitsi shops that already issue ASAP tokens.
- Trivial HMAC mode for smaller deployments.
- `PyJWT[crypto]` is added to Dockerfile + Dockerfile.cuda + both
  host venv scripts.

---

## ADR-016 — Optional LLM summarisation module

**Status:** Accepted (2026-06-01)

**Context.** Skynet bundles summaries + action items via vLLM /
Ollama / OCI. Casual-SST scoped that out originally to stay focused
on transcription, but users want a "one endpoint to summarise a
finalized transcript" path without standing up the whole Skynet
stack.

**Decision.** Single endpoint `POST /api/summarize` that takes a
transcript and returns `{summary, action_items}`. Pluggable LLM via
the `LLM_BACKEND` env var:

- `openai` — any OpenAI-format `/chat/completions` endpoint (OpenAI,
  LM Studio, vLLM with the openai-compat shim, llama.cpp's
  api-server, Groq, Fireworks, Together, …). Talk via httpx, not the
  official SDKs — keeps the image lean.
- `ollama` — Ollama's native chat API.

Off when `LLM_BACKEND` is unset — `/api/summarize` returns 503,
`/api/summarize/health` reports `configured: false`. The route
always exists so OpenAPI docs show it.

**Considered and rejected.**
- Sticky per-meeting summaries with state. Out of scope; users with
  CRM-like needs can build on top of the stateless endpoint.
- Multiple endpoints for summary vs action-items. One LLM call is
  cheaper; the response is small JSON.

**Consequences.**
- httpx added to the runtime image (≈300 KB).
- Easy to point at any local or hosted LLM without code changes.

---

## ADR-017 — CI on every push, Docker Hub publish only on `v*` tags

**Status:** Accepted (2026-06-01)

**Context.** Two related but distinct requirements: (1) prevent
broken code from landing on `main` by running tests automatically,
and (2) avoid spamming Docker Hub with one image per commit while
still making it trivial to publish a release.

**Decision.** Two GitHub Actions workflows:

  * `.github/workflows/ci.yml` — triggers on push to `main` and on
    every PR. Builds the same Docker image we ship and runs the
    pytest suite inside it via `compose.test.yaml`. No artefacts.
  * `.github/workflows/release.yml` — triggers on tag push matching
    `v*` (or manual `workflow_dispatch`). Three sequential jobs:
      1. `test` — release gate. Identical pytest run; failure
         aborts publication.
      2. `cpu-image` — `docker buildx` `linux/amd64,linux/arm64` from
         `Dockerfile`, push to Docker Hub with semver tags
         (`0.2.0`, `0.2`, `0`) + `latest`.
      3. `cuda-image` — `linux/amd64` from `Dockerfile.cuda`, push
         with `cuda-` prefix on every tag (`cuda-0.2.0`,
         `cuda-latest`, etc.).

`docker/metadata-action@v5` generates tags; `docker/build-push-action@v6`
publishes with GHA cache (`type=gha,scope=cpu` / `scope=cuda`) so
incremental tag pushes are fast.

**Considered and rejected.**
- Publishing every push to `main` with the commit SHA. Pollutes
  Docker Hub and confuses pinning.
- A single workflow file with conditional jobs. Less readable than
  two files with clear-cut triggers.
- Using `:latest` from every release branch. We only publish from
  semver tags; branches don't produce images.

**Consequences.**
- Operators can `docker pull schnsrw/casual-sst:0.2.0` for an
  immutable artefact, or `:latest` for "newest release".
- A bad tag breaks the published `:latest` until rolled forward —
  document a tag-then-release-notes flow if that becomes an issue.
- Required GitHub config: `vars.DOCKERHUB_USERNAME`,
  `vars.DOCKERHUB_REPO` (optional, defaults to `casual-sst`),
  `secrets.DOCKERHUB_TOKEN`.
