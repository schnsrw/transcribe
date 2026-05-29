from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..types import ASRResult
from .base import NativeStreamingBackend


@dataclass
class _IndicStream:
    language: str | None
    buffer: bytearray = field(default_factory=bytearray)


class IndicConformerBackend(NativeStreamingBackend):
    """
    AI4Bharat IndicConformer — Indic languages including code-switching
    (Hinglish, Tanglish, Banglish). Supports script-mixed output by default;
    set `romanize_output: true` to get Romanized text.

    Covered langs (per AI4Bharat indic-conformer-600m-multilingual):
      hi, bn, ta, te, mr, pa, gu, kn, ml, or, as, ne, sa, sd, ur,
      and code-switched variants (hi-en, ta-en, bn-en, ...).

    NOTE: This is a stub. Real implementation:
      1. Install via AI4Bharat docs (NeMo-based).
      2. Use the multilingual checkpoint; pass lang_id per request.
      3. For code-switch (hi-en etc.), enable `code_switch` mode if available,
         otherwise route to the bilingual checkpoint.
      4. romanize_output → apply IndicTrans/itrans transliteration on Devanagari words.
    """
    name = "indic_conformer"
    SUPPORTED = {
        "hi", "bn", "ta", "te", "mr", "pa", "gu", "kn", "ml", "or", "as",
        "ne", "sa", "sd", "ur",
        "hinglish", "hi-en", "ta-en", "bn-en", "te-en", "mr-en",
    }

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_name = config.get(
            "model", "ai4bharat/indic-conformer-600m-multilingual",
        )
        self.code_switch = bool(config.get("code_switch", True))
        self.romanize_output = bool(config.get("romanize_output", False))
        self._loaded = False

    async def open_stream(self, language: str | None) -> _IndicStream:
        if not self._loaded:
            raise NotImplementedError(
                "IndicConformerBackend is a stub. Install AI4Bharat IndicConformer "
                f"({self.model_name}); set code_switch={self.code_switch}."
            )
        return _IndicStream(language=language)

    async def feed(self, h: _IndicStream, pcm: bytes) -> AsyncIterator[ASRResult]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def force_final(self, h: _IndicStream) -> ASRResult | None:
        raise NotImplementedError

    async def close(self, h: _IndicStream) -> None:
        return None
