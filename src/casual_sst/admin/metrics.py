"""
In-process counters / gauges / recent-event ring buffers.

A single ``Metrics`` singleton (``METRICS``) is mutated by the
pipeline as transcription events are emitted. The admin API reads it
to render the dashboard.

No external dependency — keeps the service self-contained. Hook a
real Prometheus client in if/when you outgrow this.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Metrics:
    """In-memory metrics for the admin portal."""

    started_at: float = field(default_factory=time.time)
    total_meetings: int = 0
    total_events: int = 0
    total_finals: int = 0
    total_interims: int = 0
    total_language_changes: int = 0

    # Last N finalized transcriptions for the live "recent" panel.
    recent_finals: deque = field(default_factory=lambda: deque(maxlen=50))

    # Per-backend counters — useful when multiple backends are mixed.
    backend_calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    backend_total_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    backend_errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Per-language tallies — handy for spotting code-switching patterns.
    lang_distribution: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record_event(self, event: Any) -> None:
        """Hook from the meeting/participant pipeline."""
        self.total_events += 1
        kind = getattr(event, "type", "")
        if kind == "final":
            self.total_finals += 1
            text = getattr(event, "text", "") or ""
            lang = getattr(event, "language", "") or ""
            self.recent_finals.append({
                "ts": int(getattr(event, "ts", time.time() * 1000)),
                "participant_id": getattr(event, "participant_id", ""),
                "language": lang,
                "text": text[:300],
                "variance": float(getattr(event, "variance", 0.0)),
            })
            if lang:
                self.lang_distribution[lang] += 1
        elif kind == "interim":
            self.total_interims += 1
        elif kind == "language_change":
            self.total_language_changes += 1

    def record_backend_call(self, backend: str, elapsed_ms: float, error: bool = False) -> None:
        self.backend_calls[backend] += 1
        self.backend_total_ms[backend] += elapsed_ms
        if error:
            self.backend_errors[backend] += 1

    def to_dict(self) -> dict:
        uptime = int(time.time() - self.started_at)
        avg_ms = {
            name: round(self.backend_total_ms[name] / max(self.backend_calls[name], 1), 2)
            for name in self.backend_calls
        }
        return {
            "uptime_seconds": uptime,
            "started_at": int(self.started_at),
            "total_meetings": self.total_meetings,
            "total_events": self.total_events,
            "total_finals": self.total_finals,
            "total_interims": self.total_interims,
            "total_language_changes": self.total_language_changes,
            "recent_finals": list(self.recent_finals),
            "backend_calls": dict(self.backend_calls),
            "backend_avg_ms": avg_ms,
            "backend_errors": dict(self.backend_errors),
            "lang_distribution": dict(self.lang_distribution),
        }


METRICS = Metrics()
