"""
Integration tests for the per-participant pipeline.

We use the mock backends (``MockChunked`` / ``MockNative``) so the test
does not need real model weights, but every other component (router,
VAD, profile, lang_state, cut_mark, filters) is exercised for real.

Reference: docs/ARCHITECTURE.md — "Data flow — chunked path".
"""

from __future__ import annotations

import pytest

from casual_sst.participant import ParticipantState
from casual_sst.types import ASRResult, Word

from ..conftest import silence_pcm, sine_pcm
from .mocks import MockChunked, _Scripted


pytestmark = pytest.mark.asyncio


def _word(text: str, s: float, e: float, p: float = 0.92) -> Word:
    return Word(text=text, start_s=s, end_s=e, prob=p)


def _chunked_state(default_cfg, router):
    return ParticipantState.create(
        participant_id="alice",
        header_lang="en",
        cfg=default_cfg,
        router=router,
    )


async def test_chunked_emits_final_after_punctuated_high_prob(default_cfg, router, fake_vad, scripted) -> None:
    """A high-confidence transcription ending with a period should split
    into a final via cut_mark and be emitted."""
    fake_vad.set_all_speech(1.0)
    text = "This is a reasonably long sentence that ends here. "
    words = [_word(text, 0.0, 1.5)]
    # Add a continuation word to give cut_mark something to leave as interim.
    words.append(_word("more", 1.7, 1.9))
    scripted.push(ASRResult(text=text + "more", words=words, confidence=0.92))

    state = _chunked_state(default_cfg, router)
    events = await state.push(sine_pcm(1.0))

    assert any(e.type == "final" for e in events)
    final = next(e for e in events if e.type == "final")
    assert final.text.strip() == text.strip()
    assert final.language == "en"
    # Profile should have learned this final.
    assert state.profile.last_finalized_lang == "en"


async def test_chunked_drops_hallucination_phrase(default_cfg, router, fake_vad, scripted) -> None:
    """Even a 'final-looking' transcription is dropped if its text is on
    the deny-list."""
    fake_vad.set_all_speech(1.0)
    words = [_word("thank you for watching everyone today. ", 0.0, 1.2),
             _word("anyway", 1.4, 1.6)]
    scripted.push(ASRResult(text="thank you for watching everyone today", words=words, confidence=0.95))

    state = _chunked_state(default_cfg, router)
    events = await state.push(sine_pcm(1.0))
    assert events == []
    assert state.profile.finals_total == 0


async def test_short_utterance_flush_finalizes_quick_yes(default_cfg, router, fake_vad, scripted) -> None:
    """The 'user said one thing then went quiet' case. force_short_flush
    should produce a final when the buffer has >= min_speech_ms of speech.

    We script TWO model responses: the first is consumed by the initial
    push() (which emits it as an interim because a single-word result
    cannot be cut-marked into a final), the second is consumed by
    force_short_flush() which bypasses cut-mark and emits the final
    directly.
    """
    # 0.3 s of speech in a 0.5 s buffer — above min_speech_ms (250 ms).
    # Keep VAD reporting speech on the buffer through both calls so
    # force_short_flush does not bail at the min_speech_ms gate.
    fake_vad.set_segments([{"start": 0.0, "end": 0.3}])
    scripted.push(
        ASRResult(text="yes", words=[_word("yes", 0.0, 0.3, p=0.93)], confidence=0.93),
        ASRResult(text="yes", words=[_word("yes", 0.0, 0.3, p=0.93)], confidence=0.93),
    )

    state = _chunked_state(default_cfg, router)
    await state.push(sine_pcm(0.5))
    events = await state.force_short_flush()

    assert any(e.type == "final" and e.text == "yes" for e in events)


async def test_short_utterance_flush_drops_below_min_speech(default_cfg, router, fake_vad, scripted) -> None:
    """A buffer with less than min_speech_ms of actual speech is discarded
    silently — no final, no model call."""
    fake_vad.set_segments([{"start": 0.0, "end": 0.1}])   # 100 ms of speech
    state = _chunked_state(default_cfg, router)
    await state.push(sine_pcm(0.5))

    fake_vad.set_silent()
    MockChunked.calls.clear()
    events = await state.force_short_flush()

    assert events == []
    # No model call because we bailed before invoking the backend.
    assert MockChunked.calls == []


async def test_chunked_routes_by_header_language(default_cfg, router, fake_vad, scripted) -> None:
    """Pipeline asks the backend for the language declared in the Jigasi header."""
    fake_vad.set_all_speech(1.0)
    scripted.push(ASRResult(text="hola amigos. ", words=[_word("hola amigos. ", 0.0, 1.2)], confidence=0.91))

    cfg = dict(default_cfg)
    cfg["routes"] = {"es": "mock_chunked", "*": "mock_chunked"}

    state = ParticipantState.create("bob", header_lang="es", cfg=cfg, router=router)
    await state.push(sine_pcm(1.0))

    assert len(MockChunked.calls) == 1
    _, lang, _ = MockChunked.calls[0]
    assert lang == "es"
