"""
Token-bucket rate limiter for the LLM endpoint.

Protects against runaway costs / accidental abuse on
``POST /api/summarize``. In-memory only — for multi-replica deployments
put a real API gateway in front, but this is the cheap default that
prevents "I forgot to disable my for-loop" disasters.

Configured via two env vars:

  LLM_RATE_LIMIT_RPM=20      requests per minute per remote IP
  LLM_RATE_LIMIT_BURST=5     allow short bursts of N requests

Defaults are deliberately conservative — bump them for trusted
internal users.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class TokenBucket:
    """Per-key token bucket. Key is typically the remote IP."""

    rate_per_minute: int = 20
    burst: int = 5

    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    @property
    def _rate_per_second(self) -> float:
        return self.rate_per_minute / 60.0

    def allow(self, key: str) -> bool:
        """Return True if a request from ``key`` is within budget.

        Refills the bucket up to ``burst`` tokens at ``rate_per_second``.
        Decrements one token per allowed request.
        """
        now = time.time()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                self._buckets[key] = _Bucket(tokens=float(self.burst) - 1, last_refill=now)
                return True
            elapsed = now - b.last_refill
            b.tokens = min(float(self.burst), b.tokens + elapsed * self._rate_per_second)
            b.last_refill = now
            if b.tokens >= 1:
                b.tokens -= 1
                return True
            return False


def from_env() -> TokenBucket:
    return TokenBucket(
        rate_per_minute=int(os.environ.get("LLM_RATE_LIMIT_RPM", "20") or 20),
        burst=int(os.environ.get("LLM_RATE_LIMIT_BURST", "5") or 5),
    )


# Process-wide singleton — keep the bucket dict simple.
LIMITER = from_env()
