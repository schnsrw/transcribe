"""
casual_sst.main
===============

FastAPI application entry point. Mounts the WebSocket endpoint, owns the
process-wide router (and therefore the loaded backends), and runs the
1 Hz idle-flush loop that drives short-utterance finalization across all
active meetings.

Run locally:
    poetry run uvicorn casual_sst.main:app --host 0.0.0.0 --port 8000

Configuration is loaded from ``$CONFIG_PATH`` (default
``config/local.yaml``). See ``docs/ARCHITECTURE.md`` for the layering
rules and ``CLAUDE.md`` for invariants that must hold for the wire
protocol and pipeline.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .frame import DISCONNECT_BYTE
from .meeting import MeetingConnection
from .router import Router, load_config

#: Default config file. ``$CONFIG_PATH`` overrides this — used to switch
#: between ``config/local.yaml`` (dev defaults) and ``config/prod.yaml``
#: without changing application code.
CONFIG_PATH = os.environ.get("CONFIG_PATH", "config/local.yaml")

cfg = load_config(CONFIG_PATH)
router = Router(cfg)
router.load_backends()

app = FastAPI(title="Casual-SST")

# Active meetings, keyed by meeting_id. Used by the idle-flush loop to
# walk every connection at 1 Hz and finalize short utterances. Cleared on
# WebSocket close.
_meetings: dict[str, MeetingConnection] = {}
_flusher_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    """Spawn the global idle-flush loop once the event loop is running."""
    global _flusher_task
    _flusher_task = asyncio.create_task(_flush_loop())


@app.websocket("/ws/{meeting_id}")
async def ws_endpoint(
    websocket: WebSocket,
    meeting_id: str,
    auth_token: str | None = None,
) -> None:
    """Per-WebSocket handler. One open connection per meeting.

    The wire protocol is defined in ``docs/PROTOCOL.md``:
      * binary frame = 60-byte ASCII header + raw 16 kHz s16le PCM
      * single ``\\x00`` byte = graceful disconnect
      * server emits JSON ``TranscriptionEvent`` records over the same WS
    """
    # TODO: JWT validation here when cfg["server"]["bypass_auth"] is False.
    # ADR-001 keeps the auth_token query-param shape Skynet-compatible.
    await websocket.accept()
    conn = MeetingConnection(ws=websocket, meeting_id=meeting_id, cfg=cfg, router=router)
    _meetings[meeting_id] = conn
    try:
        while conn.connected:
            try:
                raw = await websocket.receive_bytes()
            except WebSocketDisconnect:
                break
            # 1-byte disconnect signal — Skynet-compatible (\x00).
            if len(raw) == 1 and raw == DISCONNECT_BYTE:
                break
            await conn.handle_frame(raw)
    finally:
        await conn.close()
        _meetings.pop(meeting_id, None)


async def _flush_loop() -> None:
    """Wake every second and let each meeting flush idle short utterances.

    Why 1 Hz: short_flush_ms defaults to 500 ms, so 1 Hz adds at most ~1 s
    of latency on top of the configured idle threshold — fast enough for
    "okay"/"yes"/"no" answers to land quickly without burning CPU on a
    tight loop.
    """
    while True:
        await asyncio.sleep(1.0)
        for conn in list(_meetings.values()):
            try:
                await conn.flush_idle()
            except Exception:
                # Per-meeting failures must not kill the global flush loop.
                # The connection's own error path will handle WS close.
                pass
