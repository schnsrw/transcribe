from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

import numpy as np

from .frame import SAMPLE_RATE, bytes_to_seconds

_tiny = None


def _load_tiny(device: str = "cpu"):
    """Lazy-load a tiny multilingual Whisper just for language detection."""
    global _tiny
    if _tiny is None:
        from faster_whisper import WhisperModel
        _tiny = WhisperModel("tiny", device=device, compute_type="int8")
    return _tiny


def _to_float32(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0


@dataclass
class LIDResult:
    language: str
    probability: float


def detect_sync(pcm: bytes, device: str = "cpu") -> LIDResult:
    model = _load_tiny(device)
    audio = _to_float32(pcm)
    lang, prob, _ = model.detect_language(audio)
    return LIDResult(language=lang, probability=float(prob))


class LIDWorker:
    """
    Background, per-participant worker. Caller pushes PCM chunks via .feed().
    When accumulated speech reaches `interval_speech_ms`, runs LID on the
    last `window_ms` of audio and invokes `on_detection(LIDResult)`.
    """

    def __init__(
        self,
        on_detection,
        interval_speech_ms: int = 3000,
        window_ms: int = 5000,
        device: str = "cpu",
    ):
        self._on_detection = on_detection
        self._interval = interval_speech_ms / 1000.0
        self._window_bytes = int((window_ms / 1000.0) * SAMPLE_RATE * 2)
        self._device = device
        self._buf: deque[bytes] = deque()
        self._buf_bytes = 0
        self._speech_acc_s = 0.0
        self._lock = asyncio.Lock()
        self._inflight = False

    def feed(self, pcm: bytes, speech_s: float) -> None:
        self._buf.append(pcm)
        self._buf_bytes += len(pcm)
        while self._buf_bytes > self._window_bytes and self._buf:
            old = self._buf.popleft()
            self._buf_bytes -= len(old)
        self._speech_acc_s += speech_s

    def should_run(self) -> bool:
        return (not self._inflight) and self._speech_acc_s >= self._interval and self._buf_bytes > 0

    async def maybe_run(self) -> LIDResult | None:
        if not self.should_run():
            return None
        async with self._lock:
            self._inflight = True
            self._speech_acc_s = 0.0
            pcm = b"".join(self._buf)
        try:
            result = await asyncio.to_thread(detect_sync, pcm, self._device)
            await self._on_detection(result)
            return result
        finally:
            self._inflight = False
