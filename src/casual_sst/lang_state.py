"""
casual_sst.lang_state
=====================

Per-participant language state machine.

States:

  ``LOCKED``        — single language; LID can promote a switch.
  ``SWITCHED``      — a switch just happened; the participant pipeline
                       consumes this mode to flush the old backend and
                       open a new one, then flips back to ``LOCKED``.
  ``MULTILINGUAL``  — code-switching active (header was ``auto``,
                       ``hinglish``, ``hi-en``, etc.). LID is ignored;
                       the active backend handles mixed input itself.

Transitions are driven by ``observe(detected_lang, confidence, ...)``
with hysteresis (``switch_consecutive`` agreeing LID readings above
``switch_threshold``) and a profile-based **sticky lock** — see
ADR-004 and ADR-006.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .profile import ParticipantProfile


class RouteMode(str, Enum):
    """The three possible LangState modes — see module docstring."""
    LOCKED = "locked"
    SWITCHED = "switched"
    MULTILINGUAL = "multilingual"


@dataclass
class LangState:
    """
    Per-participant language state machine. Driven by:
      - header lang (initial)
      - background LID detections
      - participant profile (tiebreaker + sticky lock)
    """
    header_lang: str
    active_lang: str
    mode: RouteMode = RouteMode.LOCKED
    lid_history: deque = field(default_factory=lambda: deque(maxlen=10))
    last_switch_ms: int = 0
    switch_consecutive: int = 2          # N agreeing detections required to flip
    switch_threshold: float = 0.85       # min LID confidence

    @classmethod
    def from_header(cls, header_lang: str) -> "LangState":
        """Build initial state from the Jigasi header's ``lang`` value.

        Virtual language codes (``auto``, ``hi-en``, ``hinglish``, etc.)
        start the participant in ``MULTILINGUAL`` mode where LID-driven
        switching is disabled.
        """
        if header_lang in ("auto", "multi", "hi-en", "ta-en", "bn-en", "hinglish"):
            return cls(
                header_lang=header_lang,
                active_lang=header_lang,
                mode=RouteMode.MULTILINGUAL,
            )
        return cls(header_lang=header_lang, active_lang=header_lang)

    def observe(
        self,
        detected_lang: str,
        confidence: float,
        profile: ParticipantProfile,
        now_ms: int,
    ) -> tuple[bool, str | None]:
        """
        Push a LID observation. Returns (should_switch, new_lang).
        """
        if self.mode == RouteMode.MULTILINGUAL:
            return False, None
        if confidence < self.switch_threshold:
            return False, None

        self.lid_history.append((detected_lang, confidence))

        if detected_lang == self.active_lang:
            return False, None

        recent = list(self.lid_history)[-self.switch_consecutive:]
        if len(recent) < self.switch_consecutive:
            return False, None
        if not all(d == detected_lang for d, _ in recent):
            return False, None

        # sticky lock: once a participant has 30+ finals in one lang, require
        # 1 extra consecutive disagreement before we believe the switch
        if profile.locked_strong:
            required = self.switch_consecutive + 1
            recent = list(self.lid_history)[-required:]
            if len(recent) < required or not all(d == detected_lang for d, _ in recent):
                return False, None

        self.active_lang = detected_lang
        self.mode = RouteMode.SWITCHED
        self.last_switch_ms = now_ms
        return True, detected_lang
