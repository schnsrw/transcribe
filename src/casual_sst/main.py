from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .frame import DISCONNECT_BYTE
from .meeting import MeetingConnection
from .router import Router, load_config

CONFIG_PATH = os.environ.get("CONFIG_PATH", "./config.yaml")

cfg = load_config(CONFIG_PATH)
router = Router(cfg)
router.load_backends()

app = FastAPI()
_meetings: dict[str, MeetingConnection] = {}
_flusher_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _flusher_task
    _flusher_task = asyncio.create_task(_flush_loop())


@app.websocket("/ws/{meeting_id}")
async def ws_endpoint(websocket: WebSocket, meeting_id: str, auth_token: str | None = None):
    # TODO: auth_token JWT check when cfg.server.bypass_auth is False
    await websocket.accept()
    conn = MeetingConnection(ws=websocket, meeting_id=meeting_id, cfg=cfg, router=router)
    _meetings[meeting_id] = conn
    try:
        while conn.connected:
            try:
                raw = await websocket.receive_bytes()
            except WebSocketDisconnect:
                break
            if len(raw) == 1 and raw == DISCONNECT_BYTE:
                break
            await conn.handle_frame(raw)
    finally:
        await conn.close()
        _meetings.pop(meeting_id, None)


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(1.0)
        for conn in list(_meetings.values()):
            try:
                await conn.flush_idle()
            except Exception:
                pass
