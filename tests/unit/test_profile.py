"""
Unit tests for ``casual_sst.profile.ParticipantProfile``.

Verifies the per-call profile correctly:
  * accumulates language counts
  * exposes ``dominant_lang`` and ``last_finalized_lang``
  * flips ``locked_strong`` after enough finals in one language
  * caps recent_finals to the configured window
"""

from __future__ import annotations

from casual_sst.profile import ParticipantProfile
from casual_sst.types import ASRResult


def _r(text: str, conf: float = 0.9) -> ASRResult:
    return ASRResult(text=text, confidence=conf, is_final=True)


def test_initial_state_empty() -> None:
    p = ParticipantProfile()
    assert p.last_finalized_lang is None
    assert p.dominant_lang() is None
    assert p.finals_total == 0
    assert not p.locked_strong
    assert p.personalized_initial_prompt() == ""


def test_records_final_updates_all_counters() -> None:
    p = ParticipantProfile()
    p.record_final(_r("hello"), language="en", utterance_ms=1000)
    assert p.finals_total == 1
    assert p.last_finalized_lang == "en"
    assert p.lang_counts["en"] == 1
    assert p.avg_confidence == 0.9
    assert p.avg_utterance_ms == 1000


def test_recent_finals_is_capped_to_window() -> None:
    p = ParticipantProfile(recent_finals_window=3)
    for i in range(5):
        p.record_final(_r(f"line {i}"), language="en", utterance_ms=500)
    assert list(p.recent_finals) == ["line 2", "line 3", "line 4"]
    assert "line 4" in p.personalized_initial_prompt()
    assert "line 0" not in p.personalized_initial_prompt()


def test_dominant_lang_after_mixed_finals() -> None:
    p = ParticipantProfile()
    for _ in range(3): p.record_final(_r("hi"), language="en", utterance_ms=500)
    for _ in range(5): p.record_final(_r("namaste"), language="hi", utterance_ms=500)
    assert p.dominant_lang() == "hi"


def test_locked_strong_threshold() -> None:
    p = ParticipantProfile(strong_lock_after=10)
    for _ in range(9):
        p.record_final(_r("x"), language="en", utterance_ms=500)
    assert not p.locked_strong
    p.record_final(_r("x"), language="en", utterance_ms=500)
    assert p.locked_strong


def test_locked_strong_not_triggered_when_mixed() -> None:
    p = ParticipantProfile(strong_lock_after=10)
    # 5 en + 5 hi alternating → no language has 10 finals
    for i in range(10):
        p.record_final(_r("x"), language="en" if i % 2 == 0 else "hi", utterance_ms=500)
    assert not p.locked_strong
