"""
casual_sst.frame
================

Jigasi-compatible wire-format parser.

A WebSocket frame from Jigasi (and from our own demo) is a single binary
blob laid out as::

    ┌──────────────────────────┬────────────────────────────┐
    │  60-byte ASCII header    │  raw 16 kHz s16le PCM      │
    │  "participant_id|lang"   │  (no WAV header)           │
    │  null-padded             │                            │
    └──────────────────────────┴────────────────────────────┘

The header layout is **frozen** — see ``docs/PROTOCOL.md`` and ADR-001.
Anything that touches the wire (server, demo, tests, Jigasi) must agree
on this format.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Header length in bytes. Hard-coded because changing it is a protocol
#: break that requires coordinated client + server changes.
HEADER_BYTES = 60

#: Single-byte sentinel that the client sends as a graceful disconnect
#: signal. Identical to Skynet's streaming_whisper module.
DISCONNECT_BYTE = b"\x00"

#: Wire sample rate. Encoded in protocol; do not change.
SAMPLE_RATE = 16000

#: Bytes per sample at the wire format (16-bit little-endian PCM).
SAMPLE_WIDTH = 2

#: Pre-computed seconds-per-byte at the wire format. Useful for tight
#: loops where dividing every time would be wasted work.
ONE_BYTE_SECONDS = 1 / (SAMPLE_RATE * SAMPLE_WIDTH)


@dataclass
class Frame:
    """A parsed wire frame, ready for the pipeline."""
    participant_id: str
    language: str
    pcm: bytes

    @property
    def duration_s(self) -> float:
        """Audio duration in seconds, rounded to ms-resolution."""
        return round(len(self.pcm) * ONE_BYTE_SECONDS, 3)


def parse(buf: bytes) -> Frame:
    """Parse one wire frame.

    Tolerant of small client quirks (whitespace around the pipe, case in
    the language code) but strict about structural validity — anything
    less than 60 bytes or missing the pipe is a hard error.
    """
    if len(buf) < HEADER_BYTES:
        raise ValueError(f"frame too short: {len(buf)} bytes")
    header = buf[:HEADER_BYTES].decode("utf-8", errors="replace").strip("\x00").strip()
    parts = header.split("|", 1)
    if len(parts) != 2:
        raise ValueError(f"bad header: {header!r}")
    pid, lang = parts[0].strip(), parts[1].strip().lower()
    return Frame(participant_id=pid, language=lang, pcm=buf[HEADER_BYTES:])


def bytes_to_seconds(b: bytes | int) -> float:
    """Convert a PCM-byte count (or a buffer) into seconds of audio."""
    n = b if isinstance(b, int) else len(b)
    return round(n * ONE_BYTE_SECONDS, 3)


def seconds_to_bytes(s: float) -> int:
    """Inverse of ``bytes_to_seconds``. Result is **not** aligned to a
    sample boundary — callers that slice the buffer should align to a
    multiple of ``SAMPLE_WIDTH`` (the pipeline aligns to 2048 bytes; see
    ``casual_sst.participant.SLICE_ALIGN``)."""
    return int(s / ONE_BYTE_SECONDS)
