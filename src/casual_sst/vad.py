from __future__ import annotations

from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

from .frame import bytes_to_seconds

_model = None


def model():
    global _model
    if _model is None:
        _model = load_silero_vad(onnx=False)
    return _model


def _wav_header(audio_bytes: bytes, sample_rate: int = 16000) -> bytes:
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
    """Returns silero VAD speech segments in seconds. Empty list = no speech."""
    if len(audio) == 0:
        return []
    stream = _wav_header(audio) + audio
    waveform = read_audio(stream)
    return get_speech_timestamps(waveform, model=model(), threshold=threshold, return_seconds=True)


def total_speech_ms(audio: bytes, threshold: float = 0.5) -> float:
    """Sum of detected speech segment lengths, in ms."""
    segs = speech_timestamps(audio, threshold)
    return sum((s["end"] - s["start"]) for s in segs) * 1000.0


def is_silent(audio: bytes, threshold: float = 0.5) -> tuple[bool, list[dict]]:
    segs = speech_timestamps(audio, threshold)
    return (len(segs) == 0), segs
