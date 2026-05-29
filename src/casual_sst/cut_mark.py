from __future__ import annotations

from dataclasses import dataclass

from .types import Word


@dataclass
class CutMark:
    start_s: float = 0.0
    end_s: float = 0.0
    probability: float = 0.0


def _phrase_prob(words: list[Word], up_to_idx: int) -> float:
    n = up_to_idx + 1
    return sum(w.prob for w in words[:n]) / n if n > 0 else 0.0


def _biggest_gap(words: list[Word]) -> CutMark:
    """Fallback for runaway-long audio: split at the largest inter-word gap."""
    if len(words) < 2:
        return CutMark()
    best = CutMark()
    biggest = 0.0
    for i in range(1, len(words)):
        gap = words[i].start_s - words[i - 1].end_s
        if gap > biggest:
            biggest = gap
            best = CutMark(
                start_s=words[i - 1].end_s,
                end_s=words[i].start_s,
                probability=_phrase_prob(words, i - 1),
            )
    return best


def find(
    words: list[Word],
    min_chars: int = 48,
    min_probability: float = 0.7,
    force_split_after_s: float = 10.0,
) -> CutMark:
    """
    Skynet-style cut-mark: split a transcription into [final | interim] at:
      1. a punctuated word boundary with avg prob >= threshold and >= min_chars accumulated
      2. fallback to biggest inter-word gap if total audio >= force_split_after_s
    Returns CutMark() (zeros) if no split should happen.
    """
    if len(words) < 2:
        return CutMark()
    if words[-1].end_s >= force_split_after_s:
        return _biggest_gap(words)

    phrase = ""
    for i in range(len(words) - 1):
        w = words[i]
        phrase += w.text
        if len(phrase) < min_chars:
            continue
        avg_p = _phrase_prob(words, i)
        ends_punct = w.text.strip().endswith((".", "!", "?"))
        has_gap = w.end_s < words[i + 1].start_s
        if avg_p >= min_probability and ends_punct and has_gap:
            return CutMark(start_s=w.end_s, end_s=words[i + 1].start_s, probability=avg_p)
        if words[-1].end_s >= 15:
            return _biggest_gap(words)
    return CutMark()


def split(words: list[Word], mark: CutMark) -> tuple[list[Word], list[Word]]:
    """Split word list into (final_part, interim_part) at cut mark."""
    if mark.end_s == 0.0:
        return [], words
    final, interim = [], []
    for w in words:
        (final if w.end_s < mark.end_s else interim).append(w)
    return final, interim
