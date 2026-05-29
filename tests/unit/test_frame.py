"""
Unit tests for ``casual_sst.frame``.

The frame parser is the only thing standing between the Jigasi wire
protocol and the pipeline. Breakage here breaks Jigasi compatibility,
which is invariant #1 in CLAUDE.md.
"""

from __future__ import annotations

import pytest

from casual_sst.frame import (
    DISCONNECT_BYTE,
    HEADER_BYTES,
    Frame,
    bytes_to_seconds,
    parse,
    seconds_to_bytes,
)


# ---------- parse --------------------------------------------------------
def _frame(header: str, pcm: bytes = b"\x00" * 32) -> bytes:
    """Build a wire-format frame: ASCII header right-padded with NULs + PCM."""
    return header.encode("ascii").ljust(HEADER_BYTES, b"\x00") + pcm


def test_parse_basic_header() -> None:
    raw = _frame("alice|en")
    frame = parse(raw)
    assert isinstance(frame, Frame)
    assert frame.participant_id == "alice"
    assert frame.language == "en"
    assert len(frame.pcm) == 32


def test_parse_full_60_byte_header() -> None:
    pid = "x" * 50  # 50 + "|" + "en" = 53; fits in 60
    raw = _frame(f"{pid}|en")
    frame = parse(raw)
    assert frame.participant_id == pid
    assert frame.language == "en"


def test_parse_lang_lowercased() -> None:
    raw = _frame("alice|EN")
    assert parse(raw).language == "en"


def test_parse_strips_padding() -> None:
    # Spaces around the pipe should be stripped — clients in the wild
    # often emit "uuid | en" with whitespace.
    raw = _frame("alice | en")
    frame = parse(raw)
    assert frame.participant_id == "alice"
    assert frame.language == "en"


def test_parse_rejects_short_buffer() -> None:
    with pytest.raises(ValueError, match="too short"):
        parse(b"\x00" * 10)


def test_parse_rejects_missing_pipe() -> None:
    raw = b"alice".ljust(HEADER_BYTES, b"\x00") + b"\x00\x00"
    with pytest.raises(ValueError, match="bad header"):
        parse(raw)


# ---------- duration helpers ----------------------------------------------
def test_bytes_seconds_roundtrip() -> None:
    # 16 kHz × 2 bytes/sample × 1 s = 32 000 bytes
    assert bytes_to_seconds(32_000) == 1.0
    assert seconds_to_bytes(1.0) == 32_000


def test_frame_duration() -> None:
    one_sec = b"\x00" * 32_000
    frame = parse(_frame("alice|en", one_sec))
    assert frame.duration_s == 1.0


def test_disconnect_byte_constant() -> None:
    assert DISCONNECT_BYTE == b"\x00"
