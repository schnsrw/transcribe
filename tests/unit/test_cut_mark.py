"""
Unit tests for ``casual_sst.cut_mark``.

The cut-mark logic decides where to slice a Whisper transcription into
[final | interim]. This is the centre of gravity for chunked-backend
latency vs accuracy trade-offs. See docs/HALLUCINATION_GUARDS.md.
"""

from __future__ import annotations

from casual_sst.cut_mark import CutMark, find, split
from casual_sst.types import Word


def _w(text: str, start: float, end: float, prob: float = 0.9) -> Word:
    return Word(text=text, start_s=start, end_s=end, prob=prob)


# ---------- find -------------------------------------------------------
def test_returns_empty_for_too_few_words() -> None:
    assert find([]).end_s == 0.0
    assert find([_w("hi", 0.0, 0.2)]).end_s == 0.0


def test_splits_at_punctuated_high_prob_boundary() -> None:
    words = [
        _w("This ", 0.0, 0.30),
        _w("is ",   0.30, 0.50),
        _w("a ",    0.50, 0.60),
        _w("nice ", 0.60, 0.90),
        _w("test. ", 0.90, 1.40),   # >= 48 chars accumulated + ends with "."
        _w("And ",  1.60, 1.80),    # gap of 0.2 s → splittable
        _w("more.", 1.80, 2.10),
    ]
    # Pad text so we cross 48 chars before the period.
    words[4] = _w(
        "test extra extra extra extra extra extra. ", 0.90, 1.40,
    )
    mark = find(words, min_chars=48, min_probability=0.7, force_split_after_s=10)
    assert mark.end_s == 1.60          # split at the gap between word[4] and word[5]
    assert mark.start_s == 1.40
    assert mark.probability >= 0.7


def test_falls_back_to_biggest_gap_when_audio_long() -> None:
    # Audio is 12 s long → triggers the biggest-gap path unconditionally.
    words = [
        _w("alpha ",   0.0,  1.0, prob=0.95),
        _w("beta ",    1.0,  2.0, prob=0.95),
        _w("gamma ",   2.0,  3.0, prob=0.95),
        _w("delta ",   3.0,  4.0, prob=0.95),
        _w("epsilon ", 7.0, 11.5, prob=0.95),  # 3 s gap between delta and epsilon
        _w("zeta",    11.5, 12.0, prob=0.95),
    ]
    mark = find(words, force_split_after_s=10)
    assert mark.start_s == 4.0
    assert mark.end_s == 7.0


def test_does_not_split_low_probability() -> None:
    words = [
        _w("Word " * 1, 0.0, 0.5, prob=0.4),
        _w(("Word " * 12).strip() + ". ", 0.5, 1.5, prob=0.4),  # >48 chars, but low prob
        _w("More ", 1.6, 1.8, prob=0.4),
    ]
    assert find(words, min_probability=0.7, force_split_after_s=10).end_s == 0.0


# ---------- split ------------------------------------------------------
def test_split_with_zero_mark_returns_all_interim() -> None:
    words = [_w("a", 0.0, 0.2), _w("b", 0.2, 0.4)]
    final, interim = split(words, CutMark())
    assert final == []
    assert interim == words


def test_split_partitions_at_mark() -> None:
    words = [_w("a", 0.0, 0.2), _w("b", 0.5, 0.7), _w("c", 0.9, 1.1)]
    final, interim = split(words, CutMark(end_s=0.8))
    assert [w.text for w in final] == ["a", "b"]
    assert [w.text for w in interim] == ["c"]
