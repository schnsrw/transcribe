from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .frame import Frame, parse
from .participant import ParticipantState, _now_ms
from .router import Router
from .types import TranscriptionEvent


@dataclass
class MeetingConnection:
    ws: WebSocket
    meeting_id: str
    cfg: dict[str, Any]
    router: Router
    participants: dict[str, ParticipantState] = field(default_factory=dict)
    connected: bool = True

    async def handle_frame(self, raw: bytes) -> None:
        try:
            frame: Frame = parse(raw)
        except ValueError:
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
        """Per-call by the meeting-level flusher task."""
        now = _now_ms()
        short_ms = self.cfg["vad"]["short_flush_ms"]
        long_ms = self.cfg["vad"]["long_flush_ms"]
        for pid, state in list(self.participants.items()):
            idle = now - state.last_chunk_ms
            if idle < short_ms:
                continue
            events = await state.force_short_flush()
            for ev in events:
                await self._send(ev)
            if idle > long_ms:
                state._reset_buffer()

    async def close(self) -> None:
        self.connected = False
        for state in self.participants.values():
            await state.close()
        try:
            await self.ws.close()
        except Exception:
            pass

    async def _send(self, ev: TranscriptionEvent) -> None:
        try:
            await self.ws.send_json(ev.to_wire())
        except WebSocketDisconnect:
            self.connected = False
        except Exception:
            pass
