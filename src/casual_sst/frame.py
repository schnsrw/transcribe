from __future__ import annotations

from dataclasses import dataclass

HEADER_BYTES = 60
DISCONNECT_BYTE = b"\x00"
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # s16le → 2 bytes/sample
ONE_BYTE_SECONDS = 1 / (SAMPLE_RATE * SAMPLE_WIDTH)  # 0.00003125 — same as Skynet


@dataclass
class Frame:
    participant_id: str
    language: str
    pcm: bytes

    @property
    def duration_s(self) -> float:
        return round(len(self.pcm) * ONE_BYTE_SECONDS, 3)


def parse(buf: bytes) -> Frame:
    """Parses Jigasi's wire format. Backwards-compatible with Skynet's streaming_whisper."""
    if len(buf) < HEADER_BYTES:
        raise ValueError(f"frame too short: {len(buf)} bytes")
    header = buf[:HEADER_BYTES].decode("utf-8", errors="replace").strip("\x00").strip()
    parts = header.split("|", 1)
    if len(parts) != 2:
        raise ValueError(f"bad header: {header!r}")
    pid, lang = parts[0].strip(), parts[1].strip().lower()
    return Frame(participant_id=pid, language=lang, pcm=buf[HEADER_BYTES:])


def bytes_to_seconds(b: bytes | int) -> float:
    n = b if isinstance(b, int) else len(b)
    return round(n * ONE_BYTE_SECONDS, 3)


def seconds_to_bytes(s: float) -> int:
    return int(s / ONE_BYTE_SECONDS)
