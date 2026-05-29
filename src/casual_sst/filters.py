"""
casual_sst.filters
==================

Post-decode hallucination policy. Applied uniformly to every backend's
output, regardless of family. See ``docs/HALLUCINATION_GUARDS.md`` for
the full stack — this module is the *post-decode* layer.

Three drop reasons live here:

  1. **Deny-list match** (per-language + ``"*"`` wildcard) — for known
     training-corpus artefacts like "thank you for watching".
  2. **Below ``min_phrase_prob``** — confidence gate.
  3. **Repetition loop** — same token repeated ``threshold`` times in a
     row, which the compression-ratio threshold sometimes misses.
"""

from __future__ import annotations

import re

from .types import ASRResult


def is_hallucination(
    result: ASRResult,
    language: str,
    denylist: dict[str, list[str]],
    min_phrase_prob: float = 0.0,
) -> bool:
    """Return True if ``result`` should be dropped.

    Parameters
    ----------
    result : ASRResult
        What the model returned.
    language : str
        The active language (header lang or LID lang) — used to pick
        the right deny-list slice. Wildcard ``"*"`` always applies too.
    denylist : dict
        ``{lang_code -> [bad_text, ...], "*": [...]}`` from config.
    min_phrase_prob : float, optional
        Drop anything whose avg confidence is below this. Pass 0 to
        disable the gate (e.g. for non-Whisper backends that report
        less reliable confidences).
    """
    text = result.text.strip().lower()
    if not text:
        # Empty model output is never useful.
        return True

    if min_phrase_prob > 0 and result.confidence < min_phrase_prob:
        return True

    # Combine language-specific + wildcard deny-list entries.
    bans = list(denylist.get(language, [])) + list(denylist.get("*", []))
    for ban in bans:
        b = ban.strip().lower()
        if not b:
            continue
        if b == text or b in text:
            return True

    if _repeats(text, threshold=4):
        return True

    return False


# Word tokenizer that handles non-ASCII alphabets (Devanagari, Tamil, etc.).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _repeats(text: str, threshold: int) -> bool:
    """Detect ``threshold`` or more consecutive identical tokens.

    Catches Whisper's "you you you you" silence loop and related modes
    where the model decoder gets stuck on a single token.
    """
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
    """Used by the participant pipeline to *avoid seeding the next call's
    initial_prompt* with low-value finals.

    A different concern from ``is_hallucination``: a final like ". ." is
    fine to surface to the client (the deny-list catches outright
    "thank you for watching"-type junk), but we don't want it polluting
    the next decode's prompt context.
    """
    t = text.strip().lower()
    return any(b.strip().lower() in t for b in blacklist)
