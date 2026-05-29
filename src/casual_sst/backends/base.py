"""
casual_sst.backends.base
========================

Concrete ABCs that backends inherit from. The runtime contract is
specified by the ``Protocol``\\s in :mod:`casual_sst.types`; this file
provides ergonomic base classes so backends can share construction
logic and the type checker can verify completeness.

Every backend takes its config slice as a ``dict`` (the part under
``backends.<name>:`` in YAML). Backends are responsible for validating
their own config — there is no schema layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from ..types import ASRResult, StreamHandle


class BackendBase(ABC):
    """Common construction surface. Holds the backend's slice of config."""

    #: Logical backend name (matches the YAML key under ``backends:``).
    #: Subclasses set this as a class attribute.
    name: str

    def __init__(self, config: dict[str, Any]):
        self.config = config


class NativeStreamingBackend(BackendBase):
    """Base for backends that emit results *during* audio feed.

    Implementations should:
      * lazy-load model weights inside ``__init__`` or on first
        ``open_stream`` to avoid blocking server startup if multiple
        backends are configured;
      * implement ``force_final`` to drain any pending tokens (called
        on idle-flush and on language switch);
      * close cleanly in ``close`` — leaking GPU memory across calls is
        the failure mode that surfaces fastest in production.
    """

    @abstractmethod
    async def open_stream(self, language: str | None) -> StreamHandle: ...

    @abstractmethod
    async def feed(self, h: StreamHandle, pcm: bytes) -> AsyncIterator[ASRResult]: ...

    @abstractmethod
    async def force_final(self, h: StreamHandle) -> ASRResult | None: ...

    @abstractmethod
    async def close(self, h: StreamHandle) -> None: ...


class ChunkedBackend(BackendBase):
    """Base for backends that take a full audio buffer per call.

    All hallucination-guard config (``no_speech_threshold``,
    ``log_prob_threshold``, ``min_phrase_prob``, etc.) is read from
    ``self.config`` — there is no separate "policy" object.
    """

    @abstractmethod
    async def transcribe(
        self,
        pcm: bytes,
        language: str | None,
        initial_prompt: str | None = None,
    ) -> ASRResult: ...
