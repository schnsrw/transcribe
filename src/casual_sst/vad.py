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

from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

from .frame import bytes_to_seconds

# Cached model instance — silero ~2 MB binary; load once per process.
_model = None


def model():
    """Return the cached Silero VAD model, loading it on first call."""
    global _model
    if _model is None:
        _model = load_silero_vad(onnx=False)
    return _model


def _wav_header(audio_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Build a minimal RIFF/WAV header so we can hand a headerless PCM
    buffer to silero's ``read_audio`` (which expects a file-like blob)."""
    samples = len(audio_bytes) // 2
    bits_per_sample = 16
    channels = 1
    datasize = samples * channels * bits_per_sample // 8
    o = b"RIFF" + (datasize + 36).to_bytes(4, "little")
    o += b"WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little")
    o += channels.to_bytes(2, "little") + sample_rate.to_bytes(4, "little")
    o += (sample_rate * channels * bits_per_sample // 8).to_bytes(4, "little")
    o += (channels * bits_per_sample // 8).to_bytes(2, "little")
    o += bits_per_sample.to_bytes(2, "little") + b"data" + datasize.to_bytes(4, "little")
    return o


def speech_timestamps(audio: bytes, threshold: float = 0.5) -> list[dict]:
    """Return Silero's detected speech segments in **seconds**.

    Each entry is ``{'start': float, 'end': float}``. Empty list means
    "no speech detected" — the buffer is silence or pure noise.
    """
    if len(audio) == 0:
        return []
    stream = _wav_header(audio) + audio
    waveform = read_audio(stream)
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
