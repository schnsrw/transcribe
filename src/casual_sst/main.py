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
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .admin import METRICS, admin_router, install_log_handler, prom_router
from .auth import AuthError, get_validator
from .frame import DISCONNECT_BYTE
from .health import router as health_router
from .llm import llm_router
from .meeting import MeetingConnection
from .router import Router, load_config

# Wire the in-memory ring buffer that powers /admin/api/logs into the
# root logger. Safe no-op if the admin portal is disabled (token unset)
# — the buffer just collects records nobody reads.
install_log_handler(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("casual_sst")

#: Default config file. ``$CONFIG_PATH`` overrides this — used to switch
#: between ``config/local.yaml`` (dev defaults) and ``config/prod.yaml``
#: without changing application code.
CONFIG_PATH = os.environ.get("CONFIG_PATH", "config/local.yaml")

cfg = load_config(CONFIG_PATH)
router = Router(cfg)
router.load_backends()

app = FastAPI(title="Casual-SST")
# /healthz (liveness, always OK) and /health (readiness, gates traffic).
# Unauthenticated so load balancers / orchestrators can reach them.
app.include_router(health_router)
# Admin / monitoring portal. The router itself returns 404 for every
# route when ADMIN_TOKEN is unset, so this is a safe no-op by default.
app.include_router(admin_router)
# Prometheus /metrics endpoint — always on, unauthenticated (counters
# only, no PII; gate behind a reverse proxy if you don't want public).
app.include_router(prom_router)
# Optional LLM /api/summarize. Returns 503 if LLM_BACKEND is unset, so
# the endpoint always exists but is inert by default.
app.include_router(llm_router)

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
    # JWT validation when auth is enforced. Token is passed as query
    # param `auth_token=...` per ADR-001 (Skynet-compatible). Failures
    # close the socket with code 1008 (policy violation) before any
    # frame is read.
    if not cfg["server"].get("bypass_auth", True):
        try:
            get_validator().validate(auth_token)
        except AuthError as e:
            log.warning("ws auth rejected for %s: %s", meeting_id, e)
            await websocket.close(code=1008, reason=str(e))
            return
    await websocket.accept()
    conn = MeetingConnection(ws=websocket, meeting_id=meeting_id, cfg=cfg, router=router)
    _meetings[meeting_id] = conn
    METRICS.total_meetings += 1
    log.info("meeting opened: %s", meeting_id)
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
        log.info("meeting closed: %s", meeting_id)


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
