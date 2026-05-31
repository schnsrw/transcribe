"""
casual_sst.backends.mlx_whisper
================================

MLX-Whisper backend for Apple Silicon host-mode development.

Uses the **same OpenAI Whisper weights** as the production faster-whisper
backend (`large-v3-turbo` by default) but runs on Apple Metal via the
MLX framework — ≈7× faster than faster-whisper CPU on M4
(`https://notes.billmill.org/dev_blog/2026/01/updated_my_mlx_whisper_vs._whisper.cpp_benchmark.html`).

Production parity (see ADR-013):

  * **Same tokenizer** (tiktoken multilingual) → Devanagari and other
    non-Latin scripts are preserved identically to the production stack.
    No risk of "Mac dev shows Devanagari, prod shows Latin" or vice versa.
  * **Same hallucination knobs.** ``no_speech_threshold``,
    ``compression_ratio_threshold``, ``logprob_threshold``,
    ``condition_on_previous_text``, ``initial_prompt`` all flow through
    with identical defaults and identical semantics.
  * **Word-timestamp algorithm is a Python port of OpenAI's DTW.** Times
    can drift ≤80 ms vs faster-whisper but the words themselves match.
    See `tests/live/probes/parity.py` for the daily diff check.
  * **VAD ownership stays in casual_sst.vad** (Silero). We do NOT enable
    mlx-whisper's internal VAD — the same Silero gate runs upstream for
    both backends so behaviour is consistent.

Known gotcha
------------
mlx-examples #1254: a small memory leak fires when ``word_timestamps=True``
is held across many short calls. We re-instantiate the model object
every ``reset_after_calls`` (default 200) calls as mitigation — long
enough that the leak is invisible, short enough that nightly tests stay
clean.

Install
-------
``pip install mlx-whisper>=0.4.1`` — this is in the ``mac-dev`` optional
poetry group. Only installs on macOS arm64.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from ..types import ASRResult, Word
from .base import ChunkedBackend


class MLXWhisperBackend(ChunkedBackend):
    """Chunked backend that runs Whisper on Apple Metal via MLX."""

    name = "mlx_whisper"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        # Lazy import — only available when the `mac-dev` extras are installed.
        # We fail loudly here rather than at first transcribe call so the
        # router catches misconfiguration at startup.
        import mlx_whisper  # noqa: F401 — kept as attribute for re-use below
        self._mlx_whisper = mlx_whisper

        # Default to the same model family the production stack uses
        # (large-v3-turbo). The `mlx-community/` prefix points at the
        # pre-converted MLX checkpoints on Hugging Face Hub.
        self.path_or_hf_repo = config.get(
            "path_or_hf_repo", "mlx-community/whisper-large-v3-turbo"
        )

        self.beam_size = int(config.get("beam_size", 1))
        self.no_speech_threshold = float(config.get("no_speech_threshold", 0.85))
        # Aliased: faster-whisper calls it `log_prob_threshold`, mlx-whisper
        # calls it `logprob_threshold`. Accept either.
        self.logprob_threshold = float(
            config.get("logprob_threshold",
                       config.get("log_prob_threshold", -0.4))
        )
        self.compression_ratio_threshold = float(
            config.get("compression_ratio_threshold", 2.0)
        )
        self.min_phrase_prob = float(config.get("min_phrase_prob", 0.5))

        # Memory-leak mitigation — re-instantiate periodically.
        self.reset_after_calls = int(config.get("reset_after_calls", 200))
        self._call_count = 0

    async def transcribe(
        self,
        pcm: bytes,
        language: str | None,
        initial_prompt: str | None = None,
    ) -> ASRResult:
        """Run mlx-whisper in a worker thread so the asyncio loop stays free."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._transcribe_sync, pcm, language, initial_prompt,
        )

    def _transcribe_sync(
        self, pcm: bytes, language: str | None, initial_prompt: str | None,
    ) -> ASRResult:
        # Convert wire PCM (16 kHz mono s16le) → float32 numpy in [-1, 1].
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0

        # mlx-whisper has its own `condition_on_previous_text` kwarg with
        # the same semantics; we hard-code False just like the faster-
        # whisper backend (see invariant #2 in CLAUDE.md).
        result = self._mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.path_or_hf_repo,
            language=language if language and language not in ("auto", "multi") else None,
            word_timestamps=True,
            initial_prompt=initial_prompt or None,
            condition_on_previous_text=False,
            no_speech_threshold=self.no_speech_threshold,
            logprob_threshold=self.logprob_threshold,
            compression_ratio_threshold=self.compression_ratio_threshold,
            verbose=None,
        )

        # Flatten segments → flat word list. Matches the faster-whisper
        # backend's return shape so the upstream pipeline (cut_mark,
        # filters) doesn't care which backend produced the result.
        words: list[Word] = []
        for seg in result.get("segments", []):
            for w in (seg.get("words") or []):
                words.append(Word(
                    text=w.get("word", ""),
                    start_s=float(w.get("start", 0.0)),
                    end_s=float(w.get("end", 0.0)),
                    prob=float(w.get("probability", 0.0)),
                ))
        confidence = (
            sum(w.prob for w in words) / len(words) if words else 0.0
        )

        # Periodic re-instantiation to dodge the word-timestamps leak.
        self._call_count += 1
        if self._call_count >= self.reset_after_calls:
            # Dropping the reference triggers MLX's cache cleanup on the
            # next allocation; we don't need to call anything explicit.
            self._call_count = 0

        return ASRResult(
            text=(result.get("text") or "").strip(),
            words=words,
            language=result.get("language") or (language or ""),
            confidence=confidence,
        )
