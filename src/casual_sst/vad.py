"""
casual_sst.vad
==============

Silero VAD wrapper.

We treat VAD as a **pre-decode gate**: every transcription path
(chunked or native streaming) runs Silero on the working audio first to
decide whether to call the model at all, to mark long-silence regions,
and to feed the background language-ID worker the right amount of
*speech* (vs wall-clock time).

The Silero model is loaded lazily on first call so unit tests that
monkey-patch this module never have to load the .pt file.
"""

from __future__ import annotations

import numpy as np
import torch
from silero_vad import get_speech_timestamps, load_silero_vad

from .frame import bytes_to_seconds

# Cached model instance — silero ~2 MB binary; load once per process.
_model = None


def model():
    """Return the cached Silero VAD model, loading it on first call."""
    global _model
    if _model is None:
        _model = load_silero_vad(onnx=False)
    return _model


def _pcm_to_waveform(audio: bytes) -> torch.Tensor:
    """Convert raw 16 kHz mono s16le PCM into the float32 torch tensor that
    silero's ``get_speech_timestamps`` expects.

    We bypass silero's own ``read_audio`` helper because it goes through
    ``torchaudio.list_audio_backends()`` which was removed in torchaudio
    2.x. We already know the format (it is the wire format), so building
    the tensor directly is both faster and version-independent.
    """
    arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    return torch.from_numpy(arr.copy())  # copy() so torch doesn't share buffer with bytes


def speech_timestamps(audio: bytes, threshold: float = 0.5) -> list[dict]:
    """Return Silero's detected speech segments in **seconds**.

    Each entry is ``{'start': float, 'end': float}``. Empty list means
    "no speech detected" — the buffer is silence or pure noise.
    """
    if len(audio) == 0:
        return []
    waveform = _pcm_to_waveform(audio)
    return get_speech_timestamps(waveform, model=model(), threshold=threshold, return_seconds=True)


def total_speech_ms(audio: bytes, threshold: float = 0.5) -> float:
    """Sum of detected speech segment lengths, in milliseconds.

    Used by the short-utterance gate (ADR-007): we require at least
    ``min_speech_ms`` of *actual speech* before emitting a final, not
    just enough buffer.
    """
    segs = speech_timestamps(audio, threshold)
    return sum((s["end"] - s["start"]) for s in segs) * 1000.0


def is_silent(audio: bytes, threshold: float = 0.5) -> tuple[bool, list[dict]]:
    """Return ``(is_silent, segments)`` so callers can branch on either."""
    segs = speech_timestamps(audio, threshold)
    return (len(segs) == 0), segs
