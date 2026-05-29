from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, AsyncIterator


@dataclass
class Word:
    text: str
    start_s: float
    end_s: float
    prob: float


@dataclass
class ASRResult:
    text: str
    words: list[Word] = field(default_factory=list)
    language: str = ""
    confidence: float = 0.0
    is_final: bool = False


@dataclass
class TranscriptionEvent:
    id: str
    participant_id: str
    ts: int                  # ms since epoch — start of utterance
    text: str
    audio: str = ""          # base64 wav, optional
    type: str = "interim"    # "interim" | "final" | "language_change"
    variance: float = 0.0    # avg word prob
    language: str = ""

    def to_wire(self) -> dict:
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


class StreamHandle(Protocol):
    """Opaque handle held by NativeStreamingASR.feed/close."""


class NativeStreamingASR(Protocol):
    name: str

    async def open_stream(self, language: str | None) -> StreamHandle: ...
    async def feed(self, h: StreamHandle, pcm_s16le_16k: bytes) -> AsyncIterator[ASRResult]: ...
    async def force_final(self, h: StreamHandle) -> ASRResult | None: ...
    async def close(self, h: StreamHandle) -> None: ...


class ChunkedASR(Protocol):
    name: str

    async def transcribe(
        self,
        pcm_s16le_16k: bytes,
        language: str | None,
        initial_prompt: str | None = None,
    ) -> ASRResult: ...
