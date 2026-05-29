from __future__ import annotations

import re

from .types import ASRResult


def is_hallucination(
    result: ASRResult,
    language: str,
    denylist: dict[str, list[str]],
    min_phrase_prob: float = 0.0,
) -> bool:
    """
    Returns True if the result should be dropped.
    Combines:
      - exact / substring deny-list per language (+ wildcard "*")
      - minimum phrase probability gate
      - simple repetition detector (same word repeated >= 4 times)
    """
    text = result.text.strip().lower()
    if not text:
        return True

    if min_phrase_prob > 0 and result.confidence < min_phrase_prob:
        return True

    bans = list(denylist.get(language, [])) + list(denylist.get("*", []))
    for ban in bans:
        b = ban.strip().lower()
        if not b:
            continue
        if b == text or b in text:
            return True

    if _repeats(text, 4):
        return True

    return False


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _repeats(text: str, threshold: int) -> bool:
    toks = _TOKEN_RE.findall(text)
    if len(toks) < threshold:
        return False
    run, last = 1, None
    for t in toks:
        if t == last:
            run += 1
            if run >= threshold:
                return True
        else:
            run = 1
            last = t
    return False


def in_prompt_blacklist(text: str, blacklist: list[str]) -> bool:
    t = text.strip().lower()
    return any(b.strip().lower() in t for b in blacklist)
