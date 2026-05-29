from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from ..types import ASRResult, StreamHandle


class BackendBase(ABC):
    name: str

    def __init__(self, config: dict[str, Any]):
        self.config = config


class NativeStreamingBackend(BackendBase):
    @abstractmethod
    async def open_stream(self, language: str | None) -> StreamHandle: ...

    @abstractmethod
    async def feed(self, h: StreamHandle, pcm: bytes) -> AsyncIterator[ASRResult]: ...

    @abstractmethod
    async def force_final(self, h: StreamHandle) -> ASRResult | None: ...

    @abstractmethod
    async def close(self, h: StreamHandle) -> None: ...


class ChunkedBackend(BackendBase):
    @abstractmethod
    async def transcribe(
        self,
        pcm: bytes,
        language: str | None,
        initial_prompt: str | None = None,
    ) -> ASRResult: ...
