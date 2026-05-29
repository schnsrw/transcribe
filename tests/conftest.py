"""
Shared pytest configuration and fixtures.

Common helpers:
  * silence_pcm / sine_pcm — generate raw 16 kHz s16le PCM for tests
  * default_cfg            — a config dict matching ``config/base.yaml``
                             with safe defaults for tests
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Ensure ``import casual_sst...`` resolves to ./src/casual_sst regardless of
# whether the test runner was invoked from the repo root or a subdirectory.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# PCM helpers
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000


def silence_pcm(duration_s: float) -> bytes:
    """Return `duration_s` seconds of silent 16 kHz mono s16le PCM."""
    n = int(duration_s * SAMPLE_RATE)
    return b"\x00\x00" * n


def sine_pcm(duration_s: float, freq_hz: int = 440, amp: float = 0.5) -> bytes:
    """Return `duration_s` seconds of a pure sine tone (not real speech, but
    consistent waveform — enough for VAD to *see* signal in unit tests).
    """
    out = bytearray()
    n = int(duration_s * SAMPLE_RATE)
    for i in range(n):
        v = int(math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE) * amp * 32767)
        out += int(v).to_bytes(2, "little", signed=True)
    return bytes(out)


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def default_cfg() -> dict:
    """Minimal config tree that exercises every code path without relying on
    YAML on disk. Keep this in sync with ``config/base.yaml`` structurally."""
    return {
        "server": {"host": "127.0.0.1", "port": 8000, "bypass_auth": True},
        "routes": {"en": "mock_chunked", "hi": "mock_native", "*": "mock_chunked"},
        "vad": {
            "threshold": 0.5,
            "min_speech_ms": 250,
            "long_silence_ms": 1000,
            "short_flush_ms": 500,
            "long_flush_ms": 2000,
        },
        "lid": {
            "enabled": False,            # off by default in tests
            "interval_speech_ms": 3000,
            "window_ms": 5000,
            "model": "faster-whisper-tiny",
            "device": "cpu",
            "switch_threshold": 0.85,
            "switch_consecutive": 2,
            "strong_lock_after_finals": 30,
        },
        "profile": {
            "enabled": True,
            "recent_finals_window": 5,
            "use_profile_as_lid_tiebreaker": True,
        },
        "cut_mark": {
            "min_chars": 48,
            "min_probability": 0.7,
            "force_split_after_s": 10,
        },
        "initial_prompt": {
            "enabled": True,
            "max_finals": 2,
            "min_prob_for_seeding": 0.7,
            "blacklist": [". .", "..."],
        },
        "hallucination": {
            "denylist": {
                "en": ["thank you for watching", "you", "..."],
                "*": ["♪"],
            },
        },
        "backends": {
            "mock_chunked": {"kind": "chunked", "module": "tests.integration.mocks:MockChunked", "min_phrase_prob": 0.6},
            "mock_native":  {"kind": "native_streaming", "module": "tests.integration.mocks:MockNative"},
        },
    }
