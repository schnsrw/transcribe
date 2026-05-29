"""
casual_sst.profile
==================

Per-call, per-participant runtime profile.

The profile is built **only from finalized transcriptions** that pass the
hallucination filter and the ``min_prob_for_seeding`` gate. It is then
used by:

  * :mod:`casual_sst.lang_state` — as a tiebreaker when LID is uncertain
    and as the trigger for "sticky lock" (require more evidence to
    switch a confidently-locked participant);
  * the chunked-backend pipeline — as the source of a *personalized*
    initial_prompt, replacing Skynet's meeting-wide rolling tokens
    (see ADR-006).

Profiles are not persisted across WS connections. When a participant
drops and re-joins, their profile starts empty again.
"""

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
        """Update the profile with one finalized utterance.

        Callers (the participant pipeline) are responsible for filtering
        — only call this with high-confidence, non-blacklisted finals."""
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
        """True once we have ``strong_lock_after`` finals in the *same*
        language. Tells the lang-state machine to demand more evidence
        before believing a language switch."""
        if not self.last_finalized_lang or self.finals_total < self.strong_lock_after:
            return False
        return self.lang_counts[self.last_finalized_lang] >= self.strong_lock_after

    def personalized_initial_prompt(self) -> str:
        """Concatenate recent finals into a Whisper initial_prompt string."""
        return " ".join(self.recent_finals).strip()

    def dominant_lang(self) -> str | None:
        """Most frequently finalized language across the call so far."""
        if not self.lang_counts:
            return None
        return self.lang_counts.most_common(1)[0][0]
