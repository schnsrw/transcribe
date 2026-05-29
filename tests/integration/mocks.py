"""
Mock ASR backends for integration tests.

These exist to exercise ``casual_sst.participant`` end-to-end without
needing real model weights or GPUs in CI. Both backends implement the
appropriate protocol so the router can instantiate them via the normal
config path.

``MockChunked`` returns a *scripted* sequence of ASRResults — one per
call to ``transcribe`` — so each test can declare the model's output.

``MockNative`` does the same for streaming: every call to ``feed``
yields the next scripted result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from casual_sst.backends.base import ChunkedBackend, NativeStreamingBackend
from casual_sst.types import ASRResult, Word


# A class-level queue so tests can program the next result(s) without
# having to thread a reference through the router. Cleared per test by
# the ``mock_chunked_script`` / ``mock_native_script`` fixtures.
class _Scripted:
    queue: list[ASRResult] = []

    @classmethod
    def push(cls, *results: ASRResult) -> None:
        cls.queue.extend(results)

    @classmethod
    def pop(cls) -> ASRResult:
        return cls.queue.pop(0) if cls.queue else ASRResult(text="")

    @classmethod
    def clear(cls) -> None:
        cls.queue.clear()


class MockChunked(ChunkedBackend):
    """Records every call so tests can assert what the pipeline asked for."""
    name = "mock_chunked"
    calls: list[tuple[bytes, str | None, str | None]] = []

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

    async def transcribe(self, pcm, language, initial_prompt=None) -> ASRResult:
        MockChunked.calls.append((pcm, language, initial_prompt))
        return _Scripted.pop()


@dataclass
class _MockStream:
    language: str | None
    pending: list[ASRResult] = field(default_factory=list)


class MockNative(NativeStreamingBackend):
    """Yields the scripted queue across ``feed`` invocations."""
    name = "mock_native"
    streams: list[_MockStream] = []

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

    async def open_stream(self, language: str | None) -> _MockStream:
        s = _MockStream(language=language)
        MockNative.streams.append(s)
        return s

    async def feed(self, h: _MockStream, pcm: bytes) -> AsyncIterator[ASRResult]:
        # Yield every result currently in the scripted queue. Tests can
        # arrange for >1 result per feed by pushing several before calling.
        while _Scripted.queue:
            yield _Scripted.pop()

    async def force_final(self, h: _MockStream) -> ASRResult | None:
        if not _Scripted.queue:
            return None
        r = _Scripted.pop()
        r.is_final = True
        return r

    async def close(self, h: _MockStream) -> None:
        return None
