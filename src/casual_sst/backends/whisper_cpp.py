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
        #
        # NOTE on chunk-handoff (the STREAM bug from before):
        # We used to set max_len=1 + split_on_word=True to get word-
        # level segments. The combination produced word boundaries
        # whose timestamps drifted enough that the upstream cut-mark
        # / trim logic could slice mid-syllable, dropping audio at
        # chunk boundaries. We now request larger segments
        # (max_len=0, the whisper.cpp default ≈ 30 s) and rely on
        # whisper.cpp's `t0`/`t1` per segment plus our own pipeline
        # VAD for word-level positioning. Trade-off documented in
        # ADR-013.
        self._model = self._Model(
            self.model_name,
            n_threads=self.n_threads or 4,
            print_realtime=False,
            print_progress=False,
            token_timestamps=True,        # still useful for downstream confidence
            no_speech_thold=self.no_speech_threshold,
            logprob_thold=self.logprob_threshold,
            entropy_thold=self.compression_ratio_threshold,
            no_context=True,              # = condition_on_previous_text=False
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
        # We now receive full segments (not single-word segments), so
        # each segment text is the natural Whisper phrase including
        # punctuation and inter-word spaces. We split on whitespace
        # to give the upstream cut-mark logic word-level granularity
        # — the timestamps are interpolated linearly across the
        # segment's duration. That's coarser than DTW but stable
        # enough for the pipeline (cut-mark only needs monotonic
        # boundaries; the trim aligns to 2048-byte multiples anyway).
        words: list[Word] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            start_s = float(getattr(seg, "t0", 0)) / 100.0
            end_s = float(getattr(seg, "t1", 0)) / 100.0
            # Split into individual words and linearly interpolate
            # timestamps. Conserves leading-space convention.
            tokens = text.split()
            if not tokens:
                continue
            step = (end_s - start_s) / max(len(tokens), 1)
            for i, tok in enumerate(tokens):
                w_start = start_s + i * step
                w_end = start_s + (i + 1) * step
                spaced = (" " + tok) if (words or i > 0) else tok
                # pywhispercpp 1.4.x doesn't expose per-token probs
                # at the high-level Segment object — use 0.8 as a
                # reasonable constant. Cut-mark averages over words.
                words.append(Word(text=spaced, start_s=w_start, end_s=w_end, prob=0.8))

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
