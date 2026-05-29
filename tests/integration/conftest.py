"""
Integration-test fixtures.

We monkey-patch ``casual_sst.vad`` so tests do not need Silero VAD's
binary model loaded into the process. The VAD output shape is
``list[dict]`` with ``start`` and ``end`` seconds keys — we provide a
controllable replacement.
"""

from __future__ import annotations

import pytest

import casual_sst.vad as vad_module
from casual_sst.router import Router

from .mocks import MockChunked, MockNative, _Scripted


@pytest.fixture
def fake_vad(monkeypatch):
    """Drop-in for the silero VAD wrapper used by participant.py.

    By default reports the entire buffer as one speech segment. Tests
    that need "silence in / speech in" behaviour can override via
    ``fake_vad.set_segments([...])``.
    """
    class _FakeVad:
        # ``None`` means "no explicit configuration → use default behaviour";
        # ``[]`` means "explicit silence" (set_silent). Distinguishing the
        # two is important — otherwise set_silent looks identical to
        # "uninitialised" and falls through to the default-speech path.
        _segments: list[dict] | None = None

        @classmethod
        def set_segments(cls, segs: list[dict]) -> None:
            cls._segments = segs

        @classmethod
        def set_all_speech(cls, duration_s: float) -> None:
            cls._segments = [{"start": 0.0, "end": duration_s}]

        @classmethod
        def set_silent(cls) -> None:
            cls._segments = []

    def _speech_timestamps(audio, threshold=0.5):
        # Explicit configuration (including the empty list = silent) wins.
        if _FakeVad._segments is not None:
            return _FakeVad._segments
        # Default: treat the whole non-empty buffer as one speech segment.
        if not audio:
            return []
        from casual_sst.frame import bytes_to_seconds
        return [{"start": 0.0, "end": bytes_to_seconds(audio)}]

    def _total_speech_ms(audio, threshold=0.5):
        return sum((s["end"] - s["start"]) for s in _speech_timestamps(audio)) * 1000.0

    def _is_silent(audio, threshold=0.5):
        st = _speech_timestamps(audio, threshold)
        return (len(st) == 0, st)

    monkeypatch.setattr(vad_module, "speech_timestamps", _speech_timestamps)
    monkeypatch.setattr(vad_module, "total_speech_ms", _total_speech_ms)
    monkeypatch.setattr(vad_module, "is_silent", _is_silent)
    return _FakeVad


@pytest.fixture
def scripted():
    """Clear the scripted ASR queue between tests."""
    _Scripted.clear()
    MockChunked.calls.clear()
    MockNative.streams.clear()
    yield _Scripted
    _Scripted.clear()


@pytest.fixture
def router(default_cfg):
    """A real Router wired to the mock backends — no fakes here."""
    r = Router(default_cfg)
    r.load_backends()
    return r
