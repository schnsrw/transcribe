from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .profile import ParticipantProfile


class RouteMode(str, Enum):
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
