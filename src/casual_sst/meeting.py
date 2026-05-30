"""
casual_sst.meeting
==================

One :class:`MeetingConnection` per open WebSocket. Owns the participant
state map and is driven by:

  * the WebSocket handler in ``casual_sst.main`` (incoming binary frames)
  * the 1 Hz global flush loop in ``casual_sst.main`` (idle finalization)

This module is intentionally thin — almost everything interesting lives
in :mod:`casual_sst.participant`. Meeting's job is fan-out: take a
frame, look up the participant, call ``push``, and ship the resulting
events back over the WebSocket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .frame import Frame, parse
from .participant import ParticipantState, _now_ms
from .router import Router
from .types import TranscriptionEvent


@dataclass
class MeetingConnection:
    """One per WebSocket. Multiple participants share one meeting."""
    ws: WebSocket
    meeting_id: str
    cfg: dict[str, Any]
    router: Router
    participants: dict[str, ParticipantState] = field(default_factory=dict)
    connected: bool = True

    async def handle_frame(self, raw: bytes) -> None:
        """Parse ``raw``, route to the right :class:`ParticipantState`, and
        ship any emitted events back to the client.

        New participants are created on first sight — there is no separate
        "join" message.
        """
        try:
            frame: Frame = parse(raw)
        except ValueError:
            # Junk frame — protocol break. Skynet behaviour was to drop
            # silently; we do the same to be Jigasi-bug-tolerant.
            return

        state = self.participants.get(frame.participant_id)
        if state is None:
            state = ParticipantState.create(
                participant_id=frame.participant_id,
                header_lang=frame.language,
                cfg=self.cfg,
                router=self.router,
            )
            self.participants[frame.participant_id] = state

        events = await state.push(frame.pcm)
        for ev in events:
            await self._send(ev)

    async def flush_idle(self) -> None:
        """Called once per second by the global flush loop.

        For every participant that has been idle for at least
        ``short_flush_ms`` (default 500 ms) we ask the pipeline to
        finalize whatever is in its working buffer.

        We do NOT additionally ``_reset_buffer()`` after a long-idle
        threshold — an earlier version did, but that wiped buffers
        mid-transcribe (the race fires whenever a slow chunked
        transcription holds ``is_transcribing=True`` for longer than
        ``long_flush_ms``). ``force_short_flush`` already resets the
        buffer correctly on the paths that emit a final or drop a
        sub-min_speech utterance, so the separate hard-reset is both
        redundant and unsafe.
        """
        now = _now_ms()
        short_ms = self.cfg["vad"]["short_flush_ms"]
        for pid, state in list(self.participants.items()):
            idle = now - state.last_chunk_ms
            if idle < short_ms:
                continue
            events = await state.force_short_flush()
            for ev in events:
                await self._send(ev)

    async def close(self) -> None:
        """Tear down: close every native-streaming handle, then the WS."""
        self.connected = False
        for state in self.participants.values():
            await state.close()
        try:
            await self.ws.close()
        except Exception:
            # WebSocket may already be closed — best-effort cleanup.
            pass

    async def _send(self, ev: TranscriptionEvent) -> None:
        """Marshal one event over the WS. Disconnect-tolerant."""
        try:
            await self.ws.send_json(ev.to_wire())
        except WebSocketDisconnect:
            self.connected = False
        except Exception:
            # Non-disconnect errors during send shouldn't crash the
            # whole meeting — log only. Production wires this to the
            # observability stack; here we swallow.
            pass
