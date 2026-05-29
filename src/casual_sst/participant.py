from __future__ import annotations

import asyncio
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
from .types import ASRResult, TranscriptionEvent, Word

# Buffer slicing is aligned to this multiple of bytes to match Skynet's
# expectation and avoid mid-frame splits.
SLICE_ALIGN = 2048


def _uuid7_str() -> str:
    from uuid6 import uuid7
    return str(uuid7())


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ParticipantState:
    """
    One per (meeting_id, participant_id). Owns:
      - working audio buffer + VAD state
      - language state machine
      - participant profile (per-call)
      - background LID worker
      - delegation to current backend (native vs chunked)
    """
    participant_id: str
    cfg: dict[str, Any]
    router: Router

    lang_state: LangState = field(init=False)
    profile: ParticipantProfile = field(init=False)

    working_audio: bytearray = field(default_factory=bytearray)
    working_audio_starts_ms: int = 0
    last_chunk_ms: int = 0
    last_speech_end_s: float = 0.0
    is_transcribing: bool = False
    long_silence: bool = False

    transcription_id: str = field(default_factory=_uuid7_str)
    pending_prompt_finals: list[str] = field(default_factory=list)

    _lid: LIDWorker | None = None
    _stream_handle: Any | None = None  # native backend stream
    _stream_backend: BackendBinding | None = None

    @classmethod
    def create(cls, participant_id: str, header_lang: str, cfg: dict[str, Any], router: Router):
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
                device=cfg["backends"]["whisper_turbo"].get("device", "cpu") if cfg["backends"].get("whisper_turbo") else "cpu",
            )
        s.last_chunk_ms = _now_ms()
        return s

    # ---------------- public API ----------------

    async def push(self, pcm: bytes) -> list[TranscriptionEvent]:
        """Main entry: feed a chunk, return any events to broadcast."""
        self.last_chunk_ms = _now_ms()
        if not pcm:
            return []

        # VAD on the candidate buffer (current + new chunk)
        candidate = bytes(self.working_audio) + pcm
        st = vad.speech_timestamps(candidate, self.cfg["vad"]["threshold"])
        speech_now = (st[-1]["end"] if st else 0.0)
        speech_changed = bool(st) and speech_now != self.last_speech_end_s

        events: list[TranscriptionEvent] = []

        if speech_changed:
            if not self.working_audio:
                self.working_audio_starts_ms = self.last_chunk_ms - int(bytes_to_seconds(pcm) * 1000)
            self.working_audio = bytearray(candidate)
            self.last_speech_end_s = speech_now
            self.long_silence = False

            # tell LID worker how much *new speech* arrived
            new_speech_s = max(0.0, speech_now - (self.last_speech_end_s - bytes_to_seconds(pcm)))
            if self._lid is not None:
                self._lid.feed(pcm, new_speech_s)
        else:
            buf_s = bytes_to_seconds(candidate)
            if st and (buf_s - st[-1]["end"]) >= (self.cfg["vad"]["long_silence_ms"] / 1000.0):
                self.long_silence = True

        # background LID
        if self._lid is not None:
            await self._lid.maybe_run()

        if self.lang_state.mode == RouteMode.SWITCHED:
            ev = await self._handle_switch_transition()
            events.extend(ev)

        binding = self.router.for_language(self.lang_state.active_lang)

        if binding.kind == "native_streaming":
            events.extend(await self._push_native(binding, pcm))
        else:
            events.extend(await self._push_chunked(binding))

        return events

    async def force_short_flush(self) -> list[TranscriptionEvent]:
        """Called by the meeting-level flusher when a participant has gone quiet."""
        if self.is_transcribing or not self.working_audio:
            return []
        # require minimum *actual speech* before we bother
        speech_ms = vad.total_speech_ms(bytes(self.working_audio), self.cfg["vad"]["threshold"])
        if speech_ms < self.cfg["vad"]["min_speech_ms"]:
            self._reset_buffer()
            return []
        binding = self.router.for_language(self.lang_state.active_lang)
        if binding.kind == "native_streaming":
            return await self._force_final_native(binding)
        return await self._force_final_chunked(binding)

    async def close(self) -> None:
        if self._stream_handle is not None and self._stream_backend is not None:
            try:
                await self._stream_backend.instance.close(self._stream_handle)  # type: ignore
            except Exception:
                pass
        self._stream_handle = None
        self._stream_backend = None

    # ---------------- chunked path ----------------

    async def _push_chunked(self, binding: BackendBinding) -> list[TranscriptionEvent]:
        if self.is_transcribing or self.long_silence or not self.working_audio:
            return []
        if not self.lang_state.active_lang or self.lang_state.active_lang in ("auto", "multi"):
            lang = None
        else:
            lang = self.lang_state.active_lang.split("-")[0]  # 'hi-en' → 'hi' hint
        prompt = self._initial_prompt()
        result = await self._call_chunked(binding, bytes(self.working_audio), lang, prompt)
        if result is None:
            return []
        return self._emit_from_chunked(binding, result)

    async def _force_final_chunked(self, binding: BackendBinding) -> list[TranscriptionEvent]:
        if not self.working_audio:
            return []
        lang = self.lang_state.active_lang.split("-")[0] if self.lang_state.active_lang else None
        prompt = self._initial_prompt()
        result = await self._call_chunked(binding, bytes(self.working_audio), lang, prompt)
        if result is None or not result.text:
            self._reset_buffer()
            return []

        utt_ms = bytes_to_seconds(bytes(self.working_audio)) * 1000
        if filters.is_hallucination(
            result, self.lang_state.active_lang,
            self.cfg["hallucination"]["denylist"],
            min_phrase_prob=self.cfg["backends"]["whisper_turbo"].get("min_phrase_prob", 0.6),
        ):
            self._reset_buffer()
            return []

        ev = self._build_event(
            text=result.text, ts_ms=self.working_audio_starts_ms,
            final=True, variance=result.confidence,
        )
        self._record_final(result, utt_ms)
        self._reset_buffer()
        return [ev]

    async def _call_chunked(self, binding, pcm, lang, prompt) -> ASRResult | None:
        self.is_transcribing = True
        try:
            return await binding.instance.transcribe(pcm, lang, prompt)  # type: ignore
        except Exception:
            return None
        finally:
            self.is_transcribing = False

    def _emit_from_chunked(self, binding: BackendBinding, result: ASRResult) -> list[TranscriptionEvent]:
        if not result.text:
            return []
        if filters.is_hallucination(
            result, self.lang_state.active_lang,
            self.cfg["hallucination"]["denylist"],
            min_phrase_prob=self.cfg["backends"][binding.name].get("min_phrase_prob", 0.0),
        ):
            return []

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
            cut_s = mark.end_s
            cut_bytes = self._align(seconds_to_bytes(cut_s))
            if cut_bytes > 0:
                final_text = "".join(w.text for w in finals).strip()
                final_start_ms = self.working_audio_starts_ms + int(finals[0].start_s * 1000)
                self._trim_buffer(cut_bytes)
                utt_ms = (mark.end_s - finals[0].start_s) * 1000
                ev = self._build_event(
                    text=final_text, ts_ms=final_start_ms, final=True,
                    variance=mark.probability,
                )
                self._record_final(
                    ASRResult(text=final_text, words=finals, confidence=mark.probability),
                    utt_ms,
                )
                events.append(ev)
                if interims:
                    self.working_audio_starts_ms += int(mark.end_s * 1000)

        if interims:
            interim_text = "".join(w.text for w in interims).strip()
            if interim_text:
                interim_ms = self.working_audio_starts_ms + int(interims[0].start_s * 1000)
                events.append(self._build_event(
                    text=interim_text, ts_ms=interim_ms, final=False,
                    variance=result.confidence,
                ))
        return events

    # ---------------- native streaming path ----------------

    async def _push_native(self, binding: BackendBinding, pcm: bytes) -> list[TranscriptionEvent]:
        if self._stream_handle is None or self._stream_backend is not binding:
            await self.close()
            self._stream_handle = await binding.instance.open_stream(  # type: ignore
                self.lang_state.active_lang if self.lang_state.active_lang not in ("auto", "multi") else None,
            )
            self._stream_backend = binding

        events: list[TranscriptionEvent] = []
        async for result in binding.instance.feed(self._stream_handle, pcm):  # type: ignore
            if filters.is_hallucination(
                result, self.lang_state.active_lang,
                self.cfg["hallucination"]["denylist"],
            ):
                continue
            ts_ms = self.working_audio_starts_ms or self.last_chunk_ms
            ev = self._build_event(
                text=result.text, ts_ms=ts_ms, final=result.is_final,
                variance=result.confidence,
            )
            events.append(ev)
            if result.is_final:
                self._record_final(result, utterance_ms=bytes_to_seconds(pcm) * 1000)
        return events

    async def _force_final_native(self, binding: BackendBinding) -> list[TranscriptionEvent]:
        if self._stream_handle is None:
            return []
        result = await binding.instance.force_final(self._stream_handle)  # type: ignore
        if not result or not result.text:
            return []
        if filters.is_hallucination(result, self.lang_state.active_lang, self.cfg["hallucination"]["denylist"]):
            return []
        ev = self._build_event(
            text=result.text, ts_ms=self.working_audio_starts_ms or self.last_chunk_ms,
            final=True, variance=result.confidence,
        )
        self._record_final(result, utterance_ms=bytes_to_seconds(bytes(self.working_audio)) * 1000)
        self._reset_buffer()
        return [ev]

    # ---------------- LID + lang switching ----------------

    async def _on_lid(self, lid: LIDResult) -> None:
        if self.cfg["profile"]["use_profile_as_lid_tiebreaker"]:
            dominant = self.profile.dominant_lang()
            if dominant and dominant != lid.language and lid.probability < 0.95:
                # let profile dominate weak-confidence LID
                return
        self.lang_state.observe(lid.language, lid.probability, self.profile, _now_ms())

    async def _handle_switch_transition(self) -> list[TranscriptionEvent]:
        # flush whatever's pending on the OLD backend before switching
        flush = await self.force_short_flush()
        await self.close()  # drop native stream handle
        self.lang_state.mode = RouteMode.LOCKED
        event = self._build_event(
            text="", ts_ms=_now_ms(), final=False, variance=0.0,
            type_="language_change",
        )
        return flush + [event]

    # ---------------- helpers ----------------

    def _initial_prompt(self) -> str | None:
        if not self.cfg["initial_prompt"]["enabled"]:
            return None
        if self.cfg["profile"]["enabled"] and self.profile.finals_total > 0:
            return self.profile.personalized_initial_prompt() or None
        return None

    def _record_final(self, result: ASRResult, utterance_ms: float) -> None:
        if not self.cfg["profile"]["enabled"]:
            return
        if filters.in_prompt_blacklist(result.text, self.cfg["initial_prompt"]["blacklist"]):
            return
        if result.confidence < self.cfg["initial_prompt"]["min_prob_for_seeding"]:
            return
        self.profile.record_final(result, self.lang_state.active_lang, utterance_ms)

    def _build_event(
        self, text: str, ts_ms: int, final: bool, variance: float,
        type_: str | None = None,
    ) -> TranscriptionEvent:
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
        return (nbytes // SLICE_ALIGN) * SLICE_ALIGN

    def _trim_buffer(self, nbytes: int) -> None:
        if nbytes <= 0:
            return
        del self.working_audio[:nbytes]
        if not self.working_audio:
            self.working_audio_starts_ms = 0

    def _reset_buffer(self) -> None:
        self.working_audio = bytearray()
        self.working_audio_starts_ms = 0
        self.last_speech_end_s = 0.0
        self.long_silence = False
