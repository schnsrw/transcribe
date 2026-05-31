"""
In-memory log ring buffer for the admin portal.

A ``RingBufferHandler`` is installed on the root logger via
``install_log_handler``. Captures all records (above the configured
level) into a thread-safe deque so the admin endpoint can render
the last N lines without spawning shell processes or touching files.

This is the "tail -F /var/log/casual-sst" experience for operators
who only have HTTP access. Pair with a real syslog / journald sink
for long-term retention.
"""

from __future__ import annotations

import logging
import threading
from collections import deque


class RingBufferHandler(logging.Handler):
    """Thread-safe in-memory log handler."""

    def __init__(self, maxlen: int = 2000):
        super().__init__()
        self.buffer: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            with self._lock:
                self.buffer.append(entry)
        except Exception:
            self.handleError(record)

    def recent(self, n: int = 200, level: str | None = None) -> list[dict]:
        with self._lock:
            entries = list(self.buffer)
        if level:
            entries = [e for e in entries if e["level"] == level.upper()]
        return entries[-n:]


LOG_BUFFER = RingBufferHandler()


def install_log_handler(level: str = "INFO") -> None:
    """Wire the ring-buffer handler into the root logger.

    Safe to call multiple times — it removes any previous instance
    of itself first so reload doesn't pile up duplicates.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Strip any previous instance of our handler (reload-safe).
    root.handlers = [h for h in root.handlers if not isinstance(h, RingBufferHandler)]
    root.addHandler(LOG_BUFFER)
