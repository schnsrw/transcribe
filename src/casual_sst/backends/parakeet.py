from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..types import ASRResult
from .base import NativeStreamingBackend


@dataclass
class _ParakeetStream:
    language: str | None
    buffer: bytearray = field(default_factory=bytearray)


class ParakeetBackend(NativeStreamingBackend):
    """
    NVIDIA Parakeet-TDT-0.6B-v3 — frame-synchronous streaming, 25 European langs.

    NOTE: This is a stub. Real implementation steps:
      1. Lazy-load via nemo_toolkit.asr ASRModel.from_pretrained(model).
      2. Use TDT streaming decoder with chunk + left/right context windows.
      3. open_stream → NeMo streaming buffer.
      4. feed → push PCM, decode incrementally, yield interim/final.
      5. force_final → flush remaining frames.
    """
    name = "parakeet"
    SUPPORTED = {
        "en", "de", "es", "fr", "it", "nl", "pt", "ro", "pl", "cs", "sk",
        "hu", "el", "bg", "hr", "sl", "uk", "ru", "lt", "lv", "et", "fi",
        "sv", "da", "no",
    }

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_name = config.get("model", "nvidia/parakeet-tdt-0.6b-v3")
        self._loaded = False

    async def open_stream(self, language: str | None) -> _ParakeetStream:
        if not self._loaded:
            raise NotImplementedError(
                "ParakeetBackend is a stub. Install nemo_toolkit[asr] and load "
                f"{self.model_name}."
            )
        return _ParakeetStream(language=language)

    async def feed(self, h: _ParakeetStream, pcm: bytes) -> AsyncIterator[ASRResult]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def force_final(self, h: _ParakeetStream) -> ASRResult | None:
        raise NotImplementedError

    async def close(self, h: _ParakeetStream) -> None:
        return None
