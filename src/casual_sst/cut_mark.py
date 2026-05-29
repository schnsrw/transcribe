"""
casual_sst.cut_mark
===================

Where to split a Whisper transcription into ``[final | interim]``.

Applies **only to chunked backends** (Whisper-family). Native streaming
backends own their own finalization — see ADR-002.

The algorithm (faithful port from Skynet's streaming_whisper, with
tighter defaults):

  1. If the audio is "too long" (``force_split_after_s``, default 10 s,
     overridden to 8 s for whisper-turbo), fall straight to the
     biggest-gap heuristic — turbo loops on long buffers if not split.

  2. Otherwise scan word-by-word. Once we've accumulated ``min_chars``
     and find a word that
       * ends with sentence punctuation (. ! ?)
       * has avg phrase probability ≥ ``min_probability``
       * has a measurable gap before the next word,
     split there.

  3. If we ran out of words without finding a split AND total audio
     exceeds 15 s (the hard ceiling), fall back to biggest gap.

The output of ``find`` is a ``CutMark`` whose ``end_s`` field is the
time at which to slice. ``end_s == 0.0`` means "no split — emit
everything as interim".
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Word


@dataclass
class CutMark:
    """Where the cut should happen, plus the phrase probability up to
    that point so the emitted final inherits a meaningful variance."""
    start_s: float = 0.0   # end of last word of the final
    end_s: float = 0.0     # start of first word of the next interim
    probability: float = 0.0


def _phrase_prob(words: list[Word], up_to_idx: int) -> float:
    """Mean word probability over ``words[:up_to_idx+1]``."""
    n = up_to_idx + 1
    return sum(w.prob for w in words[:n]) / n if n > 0 else 0.0


def _biggest_gap(words: list[Word]) -> CutMark:
    """Pick the largest inter-word gap. Used when the audio is too long
    to wait for a punctuated boundary."""
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
    """Locate a place to split this transcription.

    Returns ``CutMark()`` (all zeros) if nothing should be split yet —
    the caller will emit everything as interim and wait for more audio.
    """
    if len(words) < 2:
        return CutMark()

    # 1. Force-split for runaway-long buffers.
    if words[-1].end_s >= force_split_after_s:
        return _biggest_gap(words)

    # 2. Scan for punctuated, high-confidence split.
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
        # 3. Safety net: even without a clean punctuated boundary, force
        #    a split once we're past ~15 s to avoid runaway interims.
        if words[-1].end_s >= 15:
            return _biggest_gap(words)
    return CutMark()


def split(words: list[Word], mark: CutMark) -> tuple[list[Word], list[Word]]:
    """Partition ``words`` into ``(final_part, interim_part)`` at ``mark``.

    When ``mark.end_s == 0.0`` (no split), the whole list goes into the
    interim partition and ``final_part`` is empty.
    """
    if mark.end_s == 0.0:
        return [], words
    final, interim = [], []
    for w in words:
        (final if w.end_s < mark.end_s else interim).append(w)
    return final, interim
