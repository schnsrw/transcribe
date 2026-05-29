"""
casual_sst.lid
==============

Background language-ID worker.

A small Whisper model (``tiny``) detects which language a participant
is speaking, on a rolling window of the most recent audio. The pipeline
pushes PCM into the worker continuously; the worker actually runs the
detection only after enough *speech* (measured by Silero VAD upstream)
has accumulated — ``interval_speech_ms``, default 3 s.

See ADR-004 for the design rationale and the trade-offs we considered.

This module is **producer/consumer**: the audio path is non-blocking
because detection runs in ``asyncio.to_thread``. An ``_inflight`` flag
prevents stacking detections on the same participant.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

import numpy as np

from .frame import SAMPLE_RATE, bytes_to_seconds

# Process-wide cache for the Whisper tiny model. Lazily loaded so unit
# tests that disable LID never pay the load cost.
_tiny = None


def _load_tiny(device: str = "cpu"):
    """Lazy-load a tiny multilingual Whisper just for language detection.

    Why ``tiny``: detection is ~30 ms on CPU and accurate enough for
    "Hindi or English?" decisions. We do NOT use this model for
    transcription — that's what the routed backends are for.
    """
    global _tiny
    if _tiny is None:
        from faster_whisper import WhisperModel
        _tiny = WhisperModel("tiny", device=device, compute_type="int8")
    return _tiny


def _to_float32(pcm: bytes) -> np.ndarray:
    """Convert 16-bit PCM → float32 [-1, 1] as faster-whisper expects."""
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0


@dataclass
class LIDResult:
    """One language-ID detection: ISO code + the model's probability."""
    language: str
    probability: float


def detect_sync(pcm: bytes, device: str = "cpu") -> LIDResult:
    """Synchronous detection. Always called via ``asyncio.to_thread`` from
    the worker to keep the event loop responsive."""
    model = _load_tiny(device)
    audio = _to_float32(pcm)
    lang, prob, _ = model.detect_language(audio)
    return LIDResult(language=lang, probability=float(prob))


class LIDWorker:
    """Per-participant LID worker.

    The host pipeline calls :meth:`feed` for every audio chunk and
    :meth:`maybe_run` once per chunk to give the worker a chance to
    detect. The worker triggers detection only when ``should_run``
    returns True, which is governed by three things:

      1. accumulated speech ≥ ``interval_speech_ms``
      2. there is buffered audio at all
      3. no detection is already in flight for this participant

    On detection, ``on_detection(LIDResult)`` is invoked as a coroutine
    — the participant pipeline uses it to push observations into its
    :class:`~casual_sst.lang_state.LangState`.
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
        # Rolling window of recent PCM chunks. Total bytes capped at
        # ``window_ms`` worth — old chunks are popped from the left.
        self._buf: deque[bytes] = deque()
        self._buf_bytes = 0
        self._speech_acc_s = 0.0
        self._lock = asyncio.Lock()
        self._inflight = False

    def feed(self, pcm: bytes, speech_s: float) -> None:
        """Append one audio chunk; trim the buffer to the configured window.

        ``speech_s`` is how much of *this* chunk was speech (per Silero
        VAD upstream). We accumulate speech time, not wall-clock time,
        so detection cadence is robust to long silences.
        """
        self._buf.append(pcm)
        self._buf_bytes += len(pcm)
        while self._buf_bytes > self._window_bytes and self._buf:
            old = self._buf.popleft()
            self._buf_bytes -= len(old)
        self._speech_acc_s += speech_s

    def should_run(self) -> bool:
        """Return True if a detection should be triggered now."""
        return (not self._inflight) and self._speech_acc_s >= self._interval and self._buf_bytes > 0

    async def maybe_run(self) -> LIDResult | None:
        """Trigger detection if ``should_run`` says so; no-op otherwise.

        Returns the detection result for convenience; the
        ``on_detection`` callback is invoked regardless.
        """
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
