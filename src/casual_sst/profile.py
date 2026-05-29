from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from .types import ASRResult


@dataclass
class ParticipantProfile:
    """
    Runtime, per-call profile built from each participant's finalized transcriptions.
    Used as:
      - LID tiebreaker (when LID is uncertain, lean towards profile.last_finalized_lang)
      - default language on reconnect
      - source for personalized initial_prompt (recent_finals)
      - sticky-lock signal once `locked_strong` flips True
    """
    recent_finals_window: int = 5
    strong_lock_after: int = 30

    last_finalized_lang: str | None = None
    lang_counts: Counter[str] = field(default_factory=Counter)
    finals_total: int = 0

    sum_confidence: float = 0.0
    sum_utterance_ms: float = 0.0

    recent_finals: deque[str] = field(default_factory=deque)

    def record_final(self, result: ASRResult, language: str, utterance_ms: float) -> None:
        self.last_finalized_lang = language
        self.lang_counts[language] += 1
        self.finals_total += 1
        self.sum_confidence += result.confidence
        self.sum_utterance_ms += utterance_ms

        self.recent_finals.append(result.text)
        while len(self.recent_finals) > self.recent_finals_window:
            self.recent_finals.popleft()

    @property
    def avg_confidence(self) -> float:
        return self.sum_confidence / self.finals_total if self.finals_total else 0.0

    @property
    def avg_utterance_ms(self) -> float:
        return self.sum_utterance_ms / self.finals_total if self.finals_total else 0.0

    @property
    def locked_strong(self) -> bool:
        if not self.last_finalized_lang or self.finals_total < self.strong_lock_after:
            return False
        return self.lang_counts[self.last_finalized_lang] >= self.strong_lock_after

    def personalized_initial_prompt(self) -> str:
        return " ".join(self.recent_finals).strip()

    def dominant_lang(self) -> str | None:
        if not self.lang_counts:
            return None
        return self.lang_counts.most_common(1)[0][0]
