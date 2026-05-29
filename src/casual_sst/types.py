"""
casual_sst.types
================

Cross-cutting type definitions used by every other module.

This file is the **boundary between the pipeline and the backends**: the
two ``Protocol`` types declared here (``NativeStreamingASR``,
``ChunkedASR``) are the only thing the pipeline expects of a model.
Adding a new backend means implementing one of them — see
``docs/DECISIONS.md`` ADR-002 for the why.

Only the standard library may be imported here; downstream modules
depend on this file freely and importing third-party packages would
risk circular import problems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


# ---------------------------------------------------------------------------
# Data classes — minimal value types passed around the pipeline.
# ---------------------------------------------------------------------------
@dataclass
class Word:
    """One transcribed word with its time interval and confidence.

    All times are in **seconds**, measured from the start of the audio
    buffer the model was given. ``prob`` is the per-word probability
    reported by the model (Whisper word_timestamps); for backends that
    do not expose per-word probabilities, treat it as the segment
    probability replicated across the segment's words.
    """
    text: str
    start_s: float
    end_s: float
    prob: float


@dataclass
class ASRResult:
    """A model's output for one decode call (chunked) or one streaming
    emission (native). Used uniformly across both backend families."""
    text: str
    words: list[Word] = field(default_factory=list)
    language: str = ""
    confidence: float = 0.0
    is_final: bool = False


@dataclass
class TranscriptionEvent:
    """What the WebSocket actually emits to the client.

    The wire shape is fixed by ``docs/PROTOCOL.md`` — fields here map
    1-to-1 to JSON keys. Do NOT rename existing fields without bumping
    the protocol version (which currently does not exist precisely
    because the format is frozen).
    """
    id: str
    participant_id: str
    ts: int                  # ms since UNIX epoch — start of this utterance
    text: str
    audio: str = ""          # base64 wav, optional
    type: str = "interim"    # "interim" | "final" | "language_change"
    variance: float = 0.0    # avg per-word probability
    language: str = ""

    def to_wire(self) -> dict:
        """Return the JSON-serializable dict sent over the WebSocket."""
        return {
            "id": self.id,
            "participant_id": self.participant_id,
            "ts": self.ts,
            "text": self.text,
            "audio": self.audio,
            "type": self.type,
            "variance": self.variance,
            "language": self.language,
        }


# ---------------------------------------------------------------------------
# Backend protocols (Strategy pattern — see ADR-002).
# ---------------------------------------------------------------------------
class StreamHandle(Protocol):
    """Opaque per-stream state held by a ``NativeStreamingASR`` backend.

    The pipeline does not introspect this — it round-trips whatever the
    backend returned from ``open_stream`` back to ``feed`` / ``close``.
    """


class NativeStreamingASR(Protocol):
    """Backend that **emits results as audio arrives**.

    Voxtral, Parakeet-TDT and AI4Bharat IndicConformer all fit this
    contract. The pipeline does no cut-mark logic for these backends;
    finalization is the model's responsibility.
    """

    name: str

    async def open_stream(self, language: str | None) -> StreamHandle: ...
    async def feed(self, h: StreamHandle, pcm_s16le_16k: bytes) -> AsyncIterator[ASRResult]: ...
    async def force_final(self, h: StreamHandle) -> ASRResult | None: ...
    async def close(self, h: StreamHandle) -> None: ...


class ChunkedASR(Protocol):
    """Backend that **takes a full buffer per call**.

    faster-whisper-turbo lives here. The pipeline owns the working-audio
    buffer, VAD gating, cut-mark split into final/interim, and idle-flush.
    See ``docs/HALLUCINATION_GUARDS.md`` for the stack.
    """

    name: str

    async def transcribe(
        self,
        pcm_s16le_16k: bytes,
        language: str | None,
        initial_prompt: str | None = None,
    ) -> ASRResult: ...
