from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from ..types import ASRResult, Word
from .base import ChunkedBackend


class WhisperTurboBackend(ChunkedBackend):
    """
    faster-whisper large-v3-turbo with the full hallucination-guard stack.

    Guards (all knobs come from config; defaults below are tuned for live calls):
      - no_speech_threshold:  0.85   (default 0.6 in faster-whisper)
      - log_prob_threshold:  -0.4   (default -1.0)
      - compression_ratio_threshold: 2.0  (default 2.4)
      - hallucination_silence_threshold: 2.0
      - condition_on_previous_text: False  (HARD-CODED; ignored if config sets True)
      - beam_size: 1
      - initial_prompt: explicit text, NEVER the model's own decoder state
      - min_phrase_prob: post-decode rejection threshold
    """
    name = "whisper_turbo"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        from faster_whisper import WhisperModel

        model_name = config.get("model", "large-v3-turbo")
        device = config.get("device", "auto")
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        self.model = WhisperModel(
            model_name,
            device=device,
            compute_type=config.get("compute_type", "int8_float16"),
        )
        self.beam_size = int(config.get("beam_size", 1))
        self.no_speech_threshold = float(config.get("no_speech_threshold", 0.85))
        self.log_prob_threshold = float(config.get("log_prob_threshold", -0.4))
        self.compression_ratio_threshold = float(config.get("compression_ratio_threshold", 2.0))
        self.hallucination_silence_threshold = float(config.get("hallucination_silence_threshold", 2.0))
        self.min_phrase_prob = float(config.get("min_phrase_prob", 0.6))
        # Streaming live calls benefit from Whisper's own VAD pruning the
        # leading / trailing silence in each chunk — the wall-clock cost
        # of running Silero internally is much less than transcribing
        # silence as audio.
        self.vad_filter = bool(config.get("vad_filter", True))

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
        segments, info = self.model.transcribe(
            audio,
            language=language if language and language not in ("auto", "multi") else None,
            task="transcribe",
            beam_size=self.beam_size,
            word_timestamps=True,
            initial_prompt=initial_prompt or None,
            condition_on_previous_text=False,
            no_speech_threshold=self.no_speech_threshold,
            log_prob_threshold=self.log_prob_threshold,
            compression_ratio_threshold=self.compression_ratio_threshold,
            hallucination_silence_threshold=self.hallucination_silence_threshold,
            vad_filter=self.vad_filter,
        )

        words: list[Word] = []
        text_parts: list[str] = []
        for seg in segments:
            text_parts.append(seg.text)
            for w in (seg.words or []):
                words.append(Word(
                    text=w.word,
                    start_s=float(w.start),
                    end_s=float(w.end),
                    prob=float(w.probability),
                ))
        confidence = sum(w.prob for w in words) / len(words) if words else 0.0

        return ASRResult(
            text="".join(text_parts).strip(),
            words=words,
            language=info.language if info else (language or ""),
            confidence=confidence,
        )
