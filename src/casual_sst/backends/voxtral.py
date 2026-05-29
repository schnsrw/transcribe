from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..types import ASRResult, Word
from .base import NativeStreamingBackend


@dataclass
class _VoxtralStream:
    """Opaque per-stream state. TODO: wrap an actual vLLM AsyncEngine session."""
    language: str | None
    pending_audio: bytearray = field(default_factory=bytearray)


class VoxtralBackend(NativeStreamingBackend):
    """
    Voxtral Mini 4B Realtime — native streaming, 13 languages.
      EN, ZH, HI, ES, AR, FR, PT, RU, DE, JA, KO, IT, NL

    Recommended runtime: vLLM with the realtime model and a configurable
    transcription delay (240ms..2.4s).

    NOTE: This is a stub. The real implementation should:
      1. Lazy-load vLLM with `mistralai/Voxtral-Mini-4B-Realtime-2602`.
      2. For open_stream: start an async generation session with audio input.
      3. For feed: push PCM chunks into the session and yield emitted tokens.
      4. For force_final: flush the session.
    """
    name = "voxtral"
    SUPPORTED = {"en", "zh", "hi", "es", "ar", "fr", "pt", "ru", "de", "ja", "ko", "it", "nl"}

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_name = config.get("model", "mistralai/Voxtral-Mini-4B-Realtime-2602")
        self.delay_ms = int(config.get("delay_ms", 480))
        self.max_model_len = int(config.get("max_model_len", 45000))
        # TODO: instantiate vLLM engine here, in a worker thread.
        self._loaded = False

    async def open_stream(self, language: str | None) -> _VoxtralStream:
        if not self._loaded:
            raise NotImplementedError(
                "VoxtralBackend is a stub. Wire vLLM with "
                f"{self.model_name} (delay_ms={self.delay_ms}) before use."
            )
        return _VoxtralStream(language=language)

    async def feed(self, h: _VoxtralStream, pcm: bytes) -> AsyncIterator[ASRResult]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def force_final(self, h: _VoxtralStream) -> ASRResult | None:
        raise NotImplementedError

    async def close(self, h: _VoxtralStream) -> None:
        return None
