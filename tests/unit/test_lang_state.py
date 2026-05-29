"""
Unit tests for ``casual_sst.lang_state.LangState``.

Covers the state machine transitions:
  LOCKED → LOCKED   (no switch — single agreeing detection)
  LOCKED → SWITCHED (two consecutive agreeing detections above threshold)
  multilingual virtual langs do not switch
  sticky-lock requires N+1 disagreements once profile is locked_strong
"""

from __future__ import annotations

from casual_sst.lang_state import LangState, RouteMode
from casual_sst.profile import ParticipantProfile
from casual_sst.types import ASRResult


def _record(p: ParticipantProfile, lang: str, n: int) -> None:
    for _ in range(n):
        p.record_final(ASRResult(text="x", confidence=0.9), language=lang, utterance_ms=500)


def test_from_header_locked_for_known_lang() -> None:
    s = LangState.from_header("en")
    assert s.mode == RouteMode.LOCKED
    assert s.active_lang == "en"


def test_from_header_multilingual_for_virtual_lang() -> None:
    for code in ("auto", "multi", "hi-en", "ta-en", "bn-en", "hinglish"):
        s = LangState.from_header(code)
        assert s.mode == RouteMode.MULTILINGUAL, f"{code} should be multilingual"


def test_single_detection_does_not_switch() -> None:
    s = LangState.from_header("en")
    p = ParticipantProfile()
    switched, _ = s.observe("ta", confidence=0.9, profile=p, now_ms=0)
    assert not switched
    assert s.active_lang == "en"


def test_two_consecutive_detections_switch() -> None:
    s = LangState.from_header("en")
    p = ParticipantProfile()
    s.observe("ta", confidence=0.9, profile=p, now_ms=0)
    switched, new_lang = s.observe("ta", confidence=0.9, profile=p, now_ms=1)
    assert switched
    assert new_lang == "ta"
    assert s.active_lang == "ta"
    assert s.mode == RouteMode.SWITCHED


def test_low_confidence_ignored() -> None:
    s = LangState.from_header("en")
    p = ParticipantProfile()
    s.observe("ta", confidence=0.5, profile=p, now_ms=0)  # below 0.85
    switched, _ = s.observe("ta", confidence=0.5, profile=p, now_ms=1)
    assert not switched
    assert s.active_lang == "en"


def test_multilingual_mode_never_switches() -> None:
    s = LangState.from_header("auto")
    p = ParticipantProfile()
    s.observe("ta", confidence=0.99, profile=p, now_ms=0)
    switched, _ = s.observe("ta", confidence=0.99, profile=p, now_ms=1)
    assert not switched
    assert s.mode == RouteMode.MULTILINGUAL


def test_sticky_lock_requires_extra_evidence() -> None:
    s = LangState.from_header("en")
    p = ParticipantProfile(strong_lock_after=2)
    _record(p, "en", 3)             # profile is now locked_strong on en
    assert p.locked_strong
    # Two agreeing detections would normally switch — should NOT here.
    s.observe("ta", confidence=0.9, profile=p, now_ms=0)
    switched, _ = s.observe("ta", confidence=0.9, profile=p, now_ms=1)
    assert not switched
    # A third agreeing detection breaks through the sticky lock.
    switched, new = s.observe("ta", confidence=0.9, profile=p, now_ms=2)
    assert switched
    assert new == "ta"
