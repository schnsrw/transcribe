"""
Unit tests for ``casual_sst.filters`` — the post-decode hallucination gate.
"""

from __future__ import annotations

from casual_sst.filters import in_prompt_blacklist, is_hallucination
from casual_sst.types import ASRResult, Word


def _r(text: str, conf: float = 0.9) -> ASRResult:
    return ASRResult(text=text, words=[], confidence=conf)


DENYLIST = {
    "en": ["thank you for watching", "you", "..."],
    "*":  ["♪"],
}


def test_drops_exact_match() -> None:
    assert is_hallucination(_r("thank you for watching"), "en", DENYLIST)


def test_drops_substring_match() -> None:
    assert is_hallucination(_r("[end] thank you for watching everyone"), "en", DENYLIST)


def test_drops_wildcard_match() -> None:
    # Hindi clip with a "♪" — wildcard entry should still catch it.
    assert is_hallucination(_r("♪"), "hi", DENYLIST)


def test_keeps_safe_text() -> None:
    assert not is_hallucination(_r("the meeting starts at three"), "en", DENYLIST)


def test_drops_below_min_prob() -> None:
    assert is_hallucination(_r("borderline", conf=0.3), "en", DENYLIST, min_phrase_prob=0.6)


def test_keeps_above_min_prob() -> None:
    assert not is_hallucination(_r("borderline", conf=0.9), "en", DENYLIST, min_phrase_prob=0.6)


def test_drops_empty_text() -> None:
    assert is_hallucination(_r(""), "en", DENYLIST)
    assert is_hallucination(_r("   "), "en", DENYLIST)


def test_drops_repetition_loops() -> None:
    # 4+ repetitions of the same token → loop hallucination.
    assert is_hallucination(_r("you you you you you"), "en", DENYLIST)


def test_keeps_legitimate_repetition() -> None:
    # 2-3 reps of "no" or "yeah" in normal conversation should pass.
    assert not is_hallucination(_r("no no no thanks"), "en", DENYLIST)


# ---------- in_prompt_blacklist -----------------------------------------
def test_prompt_blacklist_substring() -> None:
    assert in_prompt_blacklist(". .", [". .", "..."])
    assert in_prompt_blacklist("...", [". .", "..."])
    assert not in_prompt_blacklist("hello world", [". .", "..."])
