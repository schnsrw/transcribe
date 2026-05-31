"""
casual_sst.backends.whisper_cpp
================================

whisper.cpp backend via the ``pywhispercpp`` Python bindings.

Why this exists alongside ``mlx_whisper.py``: mlx-whisper has high
per-call overhead on M4 base (10 GPU cores) that doesn't amortize over
short audio. For 1 s chunks (Jigasi's reality) the chunked streaming
path stays sluggish even on warm models, especially on non-English.
whisper.cpp ships its own Metal kernels and keeps the model resident
across calls — different perf profile, often better for streaming.

Production parity:
  * **Same OpenAI weights.** We use the ggml conversions hosted by
    `ggerganov` on Hugging Face — pywhispercpp will download them on
    first call into ``~/Library/Application Support/pywhispercpp/``.
  * **Same tokenizer** as faster-whisper / mlx-whisper / openai-whisper.
    Devanagari preserved identically.
  * Word-level timestamps via ``token_timestamps + max_len=1 +
    split_on_word=True``. Time units are 10 ms (whisper.cpp's "cs"
    unit); we convert to seconds. Each word is emitted with a leading
    space so the participant pipeline's ``"".join`` reconstruction
    matches the other Whisper backends.
  * `condition_on_previous_text` is hard-coded False (invariant #2).
  * Per-word probability is **not exposed** by pywhispercpp's high-level
    API. We synthesise a per-result confidence from the global
    ``no_speech_prob`` — best available signal short of dropping to
    the C-bindings. Cut-mark only consumes the *average* over the
    finalized phrase, so this loss is bounded.

Known limitation (chunk-handoff, STREAM mode):
  whisper.cpp's word-timestamp DTW is sensitive to short audio. With
  1 s chunks the per-word boundaries drift enough that cut-mark's
  ``trim_buffer`` sometimes throws away mid-syllable audio, leading
  to garbled subsequent emits (e.g. "transcription system" → "descrip-
  tionsystem"). Use the mlx-whisper backend (config/dev-mac.yaml) for
  Mac streaming dev until this is tuned — see ADR-013.

Install
-------
``pip install pywhispercpp>=1.4.0`` — in the ``mac-dev`` optional group.
First call downloads the ggml model (~250 MB for small, ~1.5 GB for
large-v3-turbo).
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from ..types import ASRResult, Word
from .base import ChunkedBackend


class WhisperCppBackend(ChunkedBackend):
    """Chunked backend that runs whisper.cpp + Metal via pywhispercpp."""

    name = "whisper_cpp"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        # Lazy import — only available when the `mac-dev` extras are installed.
        from pywhispercpp.model import Model
        self._Model = Model

        # whisper.cpp model name OR path to a `ggml-*.bin`. The shorthand
        # ("small", "base", "large-v3-turbo") triggers pywhispercpp's
        # auto-download from ggerganov/whisper.cpp on HF.
        self.model_name = config.get("model", "small")

        self.no_speech_threshold = float(config.get("no_speech_threshold", 0.85))
        self.logprob_threshold = float(
            config.get("logprob_threshold",
                       config.get("log_prob_threshold", -0.4))
        )
        self.compression_ratio_threshold = float(
            config.get("compression_ratio_threshold", 2.0)
        )
        self.min_phrase_prob = float(config.get("min_phrase_prob", 0.5))
        self.n_threads = int(config.get("n_threads", 0))  # 0 → whisper.cpp picks

        # Construct the model once and reuse — this is the key reason
        # whisper.cpp can stream faster than mlx-whisper. The Metal
        # kernels stay warm across calls.
        self._model = self._Model(
            self.model_name,
            n_threads=self.n_threads or 4,
            print_realtime=False,
            print_progress=False,
            # Word-level timestamps: one "segment" per word.
            token_timestamps=True,
            max_len=1,
            split_on_word=True,
            # Whisper.cpp's hallucination guards.
            no_speech_thold=self.no_speech_threshold,
            logprob_thold=self.logprob_threshold,
            entropy_thold=self.compression_ratio_threshold,
            # Hard-coded — invariant #2 in CLAUDE.md.
            no_context=True,    # equivalent to condition_on_previous_text=False
        )

    async def transcribe(
        self,
        pcm: bytes,
        language: str | None,
        initial_prompt: str | None = None,
    ) -> ASRResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._transcribe_sync, pcm, language, initial_prompt,
        )

    def _transcribe_sync(
        self, pcm: bytes, language: str | None, initial_prompt: str | None,
    ) -> ASRResult:
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0

        # pywhispercpp's transcribe() accepts both file paths and numpy
        # arrays. We pass the runtime knobs that change per request;
        # the ones set at model-construction time are fixed.
        try:
            segments = self._model.transcribe(
                audio,
                language=(language if language and language not in ("auto", "multi") else "auto"),
                initial_prompt=initial_prompt or "",
            )
        except Exception:
            # whisper.cpp can throw on malformed input — degrade
            # gracefully like the faster-whisper backend.
            return ASRResult(text="", words=[], language=(language or ""), confidence=0.0)

        # Convert pywhispercpp Segment → Word.
        # `t0` / `t1` are in 10 ms units (whisper.cpp "cs").
        #
        # Convention match: faster-whisper emits each word with a
        # leading space (" quick", " brown", ...). The upstream
        # pipeline does ``"".join(w.text for w in words).strip()`` to
        # reconstruct phrases, so we MUST emit the same shape or the
        # joined text comes out as "Thequickbrownfox". whisper.cpp's
        # split-on-word mode strips the leading space, so we add it
        # back here.
        words: list[Word] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            start_s = float(getattr(seg, "t0", 0)) / 100.0
            end_s = float(getattr(seg, "t1", 0)) / 100.0
            # pywhispercpp 1.4.x doesn't expose token probabilities
            # on the high-level Segment object. Use no_speech_prob as
            # a coarse proxy when available, else 0.8 — cut-mark
            # averages over words so a constant value is acceptable.
            prob = float(getattr(seg, "p", 0.0)) or 0.8
            # Leading-space convention (matches faster-whisper).
            spaced = (" " + text) if words else text
            words.append(Word(text=spaced, start_s=start_s, end_s=end_s, prob=prob))

        if not words:
            return ASRResult(text="", words=[], language=(language or ""), confidence=0.0)

        full_text = "".join(w.text for w in words).strip()
        confidence = sum(w.prob for w in words) / len(words)
        return ASRResult(
            text=full_text,
            words=words,
            language=(language or ""),
            confidence=confidence,
        )
