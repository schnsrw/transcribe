"""
casual_sst.participant
======================

Heart of the per-participant pipeline. One ``ParticipantState`` per
``(meeting_id, participant_id)`` tuple.

This module is the only place that integrates **all** the side-effects:
working-audio buffer management, Silero VAD gating, language state
machine, background LID worker, per-call profile, cut-mark splitting
(for chunked backends), and delegation to the right backend instance
the router returns.

See ``docs/ARCHITECTURE.md`` for the data-flow diagrams. Invariants
worth remembering while editing this file:

  * ``condition_on_previous_text`` is **never** passed True to a
    chunked backend — the initial_prompt is the only context channel
    we allow.
  * Cut-mark logic is for chunked backends only. Native-streaming
    backends own finalization (see ADR-002).
  * Language can flip mid-call (LID). On a switch we drain the old
    backend (``force_short_flush``), close the native stream handle,
    then re-route the next chunk.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import cut_mark as cm
from . import filters
from . import vad
from .frame import bytes_to_seconds, seconds_to_bytes
from .lang_state import LangState, RouteMode
from .lid import LIDResult, LIDWorker
from .profile import ParticipantProfile
from .router import BackendBinding, Router
from .types import ASRResult, TranscriptionEvent

#: Buffer slicing alignment. Chosen to match Skynet's 2 KB chunk
#: alignment so partial-trim operations always land on a sample
#: boundary (2 KB = 1024 samples at 16 kHz s16le mono = ~64 ms).
SLICE_ALIGN = 2048


def _uuid7_str() -> str:
    """Time-ordered UUID v7 — useful so transcription ids sort by time."""
    from uuid6 import uuid7
    return str(uuid7())


def _now_ms() -> int:
    """Milliseconds since UNIX epoch."""
    return int(time.time() * 1000)


@dataclass
class ParticipantState:
    """All per-participant runtime state.

    Created lazily on the first frame for a new ``participant_id`` —
    see :meth:`MeetingConnection.handle_frame
    <casual_sst.meeting.MeetingConnection.handle_frame>`.
    """
    participant_id: str
    cfg: dict[str, Any]
    router: Router

    lang_state: LangState = field(init=False)
    profile: ParticipantProfile = field(init=False)

    # Working buffer + bookkeeping.
    working_audio: bytearray = field(default_factory=bytearray)
    working_audio_starts_ms: int = 0
    last_chunk_ms: int = 0
    last_speech_end_s: float = 0.0
    is_transcribing: bool = False
    long_silence: bool = False

    # Stable id for the current utterance; rotated on each final.
    transcription_id: str = field(default_factory=_uuid7_str)

    _lid: LIDWorker | None = None
    _stream_handle: Any | None = None
    _stream_backend: BackendBinding | None = None

    # -----------------------------------------------------------------
    # Construction
    # -----------------------------------------------------------------
    @classmethod
    def create(
        cls,
        participant_id: str,
        header_lang: str,
        cfg: dict[str, Any],
        router: Router,
    ) -> "ParticipantState":
        """Factory: build state and wire up the LID worker if enabled."""
        s = cls(participant_id=participant_id, cfg=cfg, router=router)
        s.lang_state = LangState.from_header(header_lang)
        s.lang_state.switch_consecutive = cfg["lid"]["switch_consecutive"]
        s.lang_state.switch_threshold = cfg["lid"]["switch_threshold"]
        s.profile = ParticipantProfile(
            recent_finals_window=cfg["profile"]["recent_finals_window"],
            strong_lock_after=cfg["lid"]["strong_lock_after_finals"],
        )
        if cfg["lid"]["enabled"]:
            s._lid = LIDWorker(
                on_detection=s._on_lid,
                interval_speech_ms=cfg["lid"]["interval_speech_ms"],
                window_ms=cfg["lid"]["window_ms"],
                device=cfg["lid"].get("device", "cpu"),
            )
        s.last_chunk_ms = _now_ms()
        return s

    # -----------------------------------------------------------------
    # Public API — called by MeetingConnection
    # -----------------------------------------------------------------
    async def push(self, pcm: bytes) -> list[TranscriptionEvent]:
        """Feed one audio chunk and return any events to broadcast.

        Steps:
          1. Run Silero VAD on (working buffer + new chunk).
          2. If new speech was detected, update buffer + LID feed.
             Otherwise, mark long_silence if appropriate.
          3. Let LID maybe-run.
          4. If LangState entered SWITCHED, flush old backend + emit
             a ``language_change`` event.
          5. Delegate to the chunked or native-streaming pipeline.
        """
        self.last_chunk_ms = _now_ms()
        if not pcm:
            return []

        # 1. VAD on candidate buffer.
        candidate = bytes(self.working_audio) + pcm
        st = vad.speech_timestamps(candidate, self.cfg["vad"]["threshold"])
        speech_now = st[-1]["end"] if st else 0.0

        # Save the previous speech-end *before* updating; we need it
        # below to compute how much new speech this chunk added.
        prev_speech_end_s = self.last_speech_end_s
        speech_changed = bool(st) and speech_now != prev_speech_end_s

        events: list[TranscriptionEvent] = []

        if speech_changed:
            if not self.working_audio:
                self.working_audio_starts_ms = (
                    self.last_chunk_ms - int(bytes_to_seconds(pcm) * 1000)
                )
            self.working_audio = bytearray(candidate)
            self.last_speech_end_s = speech_now
            self.long_silence = False

            # 2. Tell LID worker how much NEW speech this chunk contained.
            new_speech_s = max(0.0, speech_now - prev_speech_end_s)
            if self._lid is not None:
                self._lid.feed(pcm, new_speech_s)
        else:
            # Silent chunk: mark long_silence if the buffer's tail
            # silence is now longer than the configured threshold.
            buf_s = bytes_to_seconds(candidate)
            if st and (buf_s - st[-1]["end"]) >= (
                self.cfg["vad"]["long_silence_ms"] / 1000.0
            ):
                self.long_silence = True

        # 3. Let LID try to fire.
        if self._lid is not None:
            await self._lid.maybe_run()

        # 4. Handle pending language switch.
        if self.lang_state.mode == RouteMode.SWITCHED:
            events.extend(await self._handle_switch_transition())

        # 5. Delegate to backend.
        binding = self.router.for_language(self.lang_state.active_lang)
        if binding.kind == "native_streaming":
            events.extend(await self._push_native(binding, pcm))
        else:
            events.extend(await self._push_chunked(binding))

        return events

    async def force_short_flush(self) -> list[TranscriptionEvent]:
        """Idle-driven finalization — see ADR-007 and ``MeetingConnection.flush_idle``.

        Bails early without touching the model when:
          * a transcription is already in flight;
          * the working buffer is empty;
          * Silero VAD reports less than ``min_speech_ms`` of actual
            speech in the buffer (so coughs / breaths are dropped).
        """
        if self.is_transcribing or not self.working_audio:
            return []

        speech_ms = vad.total_speech_ms(
            bytes(self.working_audio), self.cfg["vad"]["threshold"]
        )
        if speech_ms < self.cfg["vad"]["min_speech_ms"]:
            self._reset_buffer()
            return []

        binding = self.router.for_language(self.lang_state.active_lang)
        if binding.kind == "native_streaming":
            return await self._force_final_native(binding)
        return await self._force_final_chunked(binding)

    async def close(self) -> None:
        """Release any held native-streaming handle. Safe to call twice."""
        if self._stream_handle is not None and self._stream_backend is not None:
            try:
                await self._stream_backend.instance.close(self._stream_handle)  # type: ignore
            except Exception:
                # Closing a half-broken handle should not crash teardown.
                pass
        self._stream_handle = None
        self._stream_backend = None

    # -----------------------------------------------------------------
    # Chunked backend path (Whisper-family)
    # -----------------------------------------------------------------
    async def _push_chunked(self, binding: BackendBinding) -> list[TranscriptionEvent]:
        """Run one chunked decode + cut-mark split.

        Skips work if a decode is already inflight, the participant has
        long silence pending, or the buffer is empty.
        """
        if self.is_transcribing or self.long_silence or not self.working_audio:
            return []
        lang = self._model_language_hint()
        prompt = self._initial_prompt()
        result = await self._call_chunked(binding, bytes(self.working_audio), lang, prompt)
        if result is None:
            return []
        return self._emit_from_chunked(binding, result)

    async def _force_final_chunked(self, binding: BackendBinding) -> list[TranscriptionEvent]:
        """Idle-flush variant for chunked backends.

        Skips cut-mark — the entire buffer is treated as one final
        because we are by definition past the end of speech.
        """
        if not self.working_audio:
            return []
        lang = self._model_language_hint()
        prompt = self._initial_prompt()
        result = await self._call_chunked(binding, bytes(self.working_audio), lang, prompt)
        if result is None or not result.text:
            self._reset_buffer()
            return []

        utt_ms = bytes_to_seconds(bytes(self.working_audio)) * 1000
        if filters.is_hallucination(
            result,
            self.lang_state.active_lang,
            self.cfg["hallucination"]["denylist"],
            min_phrase_prob=binding.config.get("min_phrase_prob", 0.0),
        ):
            self._reset_buffer()
            return []

        ev = self._build_event(
            text=result.text,
            ts_ms=self.working_audio_starts_ms,
            final=True,
            variance=result.confidence,
        )
        self._record_final(result, utt_ms)
        self._reset_buffer()
        return [ev]

    async def _call_chunked(
        self, binding: BackendBinding, pcm: bytes, lang: str | None, prompt: str | None,
    ) -> ASRResult | None:
        """Thin wrapper that flips ``is_transcribing`` and catches errors.

        Errors are swallowed here so a single bad chunk doesn't tear
        down the participant. The chunk is dropped, the buffer is
        preserved, and the next chunk will retry. ADR-008: we do NOT
        fall back to a different backend on error.
        """
        self.is_transcribing = True
        try:
            return await binding.instance.transcribe(pcm, lang, prompt)  # type: ignore
        except Exception:
            return None
        finally:
            self.is_transcribing = False

    def _emit_from_chunked(
        self, binding: BackendBinding, result: ASRResult,
    ) -> list[TranscriptionEvent]:
        """Apply hallucination filter + cut-mark + emit events."""
        if not result.text:
            return []
        if filters.is_hallucination(
            result,
            self.lang_state.active_lang,
            self.cfg["hallucination"]["denylist"],
            min_phrase_prob=binding.config.get("min_phrase_prob", 0.0),
        ):
            return []

        # Per-backend override (whisper_turbo uses a stricter 8 s) wins
        # over the global cut_mark default (10 s).
        force_s = float(
            binding.config.get("force_split_after_s")
            or self.cfg["cut_mark"]["force_split_after_s"]
        )
        mark = cm.find(
            result.words,
            min_chars=self.cfg["cut_mark"]["min_chars"],
            min_probability=self.cfg["cut_mark"]["min_probability"],
            force_split_after_s=force_s,
        )
        finals, interims = cm.split(result.words, mark)
        events: list[TranscriptionEvent] = []

        if finals:
            cut_bytes = self._align(seconds_to_bytes(mark.end_s))
            if cut_bytes > 0:
                final_text = "".join(w.text for w in finals).strip()
                final_start_ms = (
                    self.working_audio_starts_ms + int(finals[0].start_s * 1000)
                )
                self._trim_buffer(cut_bytes)
                utt_ms = (mark.end_s - finals[0].start_s) * 1000
                ev = self._build_event(
                    text=final_text,
                    ts_ms=final_start_ms,
                    final=True,
                    variance=mark.probability,
                )
                self._record_final(
                    ASRResult(text=final_text, words=finals, confidence=mark.probability),
                    utt_ms,
                )
                events.append(ev)
                if interims:
                    # Advance the buffer-start timeline so the next
                    # interim's timestamp is correct relative to wall clock.
                    self.working_audio_starts_ms += int(mark.end_s * 1000)

        if interims:
            interim_text = "".join(w.text for w in interims).strip()
            if interim_text:
                interim_ms = (
                    self.working_audio_starts_ms + int(interims[0].start_s * 1000)
                )
                events.append(self._build_event(
                    text=interim_text,
                    ts_ms=interim_ms,
                    final=False,
                    variance=result.confidence,
                ))
        return events

    # -----------------------------------------------------------------
    # Native streaming backend path (Voxtral / Parakeet / IndicConformer)
    # -----------------------------------------------------------------
    async def _push_native(
        self, binding: BackendBinding, pcm: bytes,
    ) -> list[TranscriptionEvent]:
        """Push one chunk into the live native-streaming session and
        consume any incrementally-emitted ASRResults.

        Opens a fresh stream lazily on first chunk OR when the routing
        decision changes (different backend instance).
        """
        if self._stream_handle is None or self._stream_backend is not binding:
            await self.close()
            self._stream_handle = await binding.instance.open_stream(  # type: ignore
                self._model_language_hint()
            )
            self._stream_backend = binding

        events: list[TranscriptionEvent] = []
        async for result in binding.instance.feed(self._stream_handle, pcm):  # type: ignore
            if filters.is_hallucination(
                result,
                self.lang_state.active_lang,
                self.cfg["hallucination"]["denylist"],
            ):
                continue
            ts_ms = self.working_audio_starts_ms or self.last_chunk_ms
            ev = self._build_event(
                text=result.text,
                ts_ms=ts_ms,
                final=result.is_final,
                variance=result.confidence,
            )
            events.append(ev)
            if result.is_final:
                self._record_final(result, utterance_ms=bytes_to_seconds(pcm) * 1000)
        return events

    async def _force_final_native(
        self, binding: BackendBinding,
    ) -> list[TranscriptionEvent]:
        """Idle-flush variant for native-streaming backends.

        Asks the model to emit whatever is pending; resets local state.
        """
        if self._stream_handle is None:
            return []
        result = await binding.instance.force_final(self._stream_handle)  # type: ignore
        if not result or not result.text:
            return []
        if filters.is_hallucination(
            result, self.lang_state.active_lang, self.cfg["hallucination"]["denylist"]
        ):
            return []
        ev = self._build_event(
            text=result.text,
            ts_ms=self.working_audio_starts_ms or self.last_chunk_ms,
            final=True,
            variance=result.confidence,
        )
        self._record_final(
            result, utterance_ms=bytes_to_seconds(bytes(self.working_audio)) * 1000
        )
        self._reset_buffer()
        return [ev]

    # -----------------------------------------------------------------
    # LID + lang switching
    # -----------------------------------------------------------------
    async def _on_lid(self, lid: LIDResult) -> None:
        """Callback wired into :class:`LIDWorker`. Applies the profile
        tiebreaker (ADR-006) before pushing into the state machine.
        """
        if self.cfg["profile"]["use_profile_as_lid_tiebreaker"]:
            dominant = self.profile.dominant_lang()
            if dominant and dominant != lid.language and lid.probability < 0.95:
                # Profile disagrees with weak LID → trust the profile.
                return
        self.lang_state.observe(
            lid.language, lid.probability, self.profile, _now_ms()
        )

    async def _handle_switch_transition(self) -> list[TranscriptionEvent]:
        """Drain old backend, close native handle, emit ``language_change``.

        Called when the LangState reports ``SWITCHED``. We flip the
        mode back to LOCKED at the end so future chunks go through
        the new active backend.
        """
        flush = await self.force_short_flush()
        await self.close()
        self.lang_state.mode = RouteMode.LOCKED
        event = self._build_event(
            text="",
            ts_ms=_now_ms(),
            final=False,
            variance=0.0,
            type_="language_change",
        )
        return flush + [event]

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------
    def _model_language_hint(self) -> str | None:
        """Pick the language hint to pass to the model.

        Rules:
          * MULTILINGUAL mode → ``None`` (let the model auto-detect /
            code-switch). Covers ``auto``, ``multi``, ``hi-en``,
            ``ta-en``, ``bn-en``, ``hinglish``.
          * LOCKED on a bilingual code (e.g. ``hi-en`` if someone
            forced LOCKED via custom config) → use the first half.
          * Otherwise → the active language as-is.
        """
        if self.lang_state.mode == RouteMode.MULTILINGUAL:
            return None
        lang = self.lang_state.active_lang
        if not lang:
            return None
        if "-" in lang:
            return lang.split("-", 1)[0]
        return lang

    def _initial_prompt(self) -> str | None:
        """Personalized initial_prompt from the profile, or None.

        Replaces Skynet's meeting-wide rolling prompt — see ADR-006.
        """
        if not self.cfg["initial_prompt"]["enabled"]:
            return None
        if self.cfg["profile"]["enabled"] and self.profile.finals_total > 0:
            return self.profile.personalized_initial_prompt() or None
        return None

    def _record_final(self, result: ASRResult, utterance_ms: float) -> None:
        """Update the participant profile, filtered by blacklist + min prob.

        Low-confidence finals MUST NOT pollute the next call's prompt
        — see ``docs/HALLUCINATION_GUARDS.md``.
        """
        if not self.cfg["profile"]["enabled"]:
            return
        if filters.in_prompt_blacklist(
            result.text, self.cfg["initial_prompt"]["blacklist"]
        ):
            return
        if result.confidence < self.cfg["initial_prompt"]["min_prob_for_seeding"]:
            return
        self.profile.record_final(result, self.lang_state.active_lang, utterance_ms)

    def _build_event(
        self,
        text: str,
        ts_ms: int,
        final: bool,
        variance: float,
        type_: str | None = None,
    ) -> TranscriptionEvent:
        """Build the wire event and rotate the transcription id on final."""
        tid = self.transcription_id
        if final:
            self.transcription_id = _uuid7_str()
        return TranscriptionEvent(
            id=tid,
            participant_id=self.participant_id,
            ts=ts_ms,
            text=text,
            type=type_ or ("final" if final else "interim"),
            variance=variance,
            language=self.lang_state.active_lang,
        )

    @staticmethod
    def _align(nbytes: int) -> int:
        """Round ``nbytes`` down to the nearest multiple of ``SLICE_ALIGN``."""
        return (nbytes // SLICE_ALIGN) * SLICE_ALIGN

    def _trim_buffer(self, nbytes: int) -> None:
        """Drop the first ``nbytes`` of the working buffer in place."""
        if nbytes <= 0:
            return
        del self.working_audio[:nbytes]
        if not self.working_audio:
            self.working_audio_starts_ms = 0

    def _reset_buffer(self) -> None:
        """Clear the working buffer and all derived bookkeeping."""
        self.working_audio = bytearray()
        self.working_audio_starts_ms = 0
        self.last_speech_end_s = 0.0
        self.long_silence = False
