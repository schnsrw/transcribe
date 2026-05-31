"""
Admin / monitoring HTTP surface.

Mounted at ``/admin`` from ``casual_sst.main``. Disabled (returns 404)
when ``ADMIN_TOKEN`` is unset.

Endpoints:
    GET  /admin/                  HTML dashboard
    GET  /admin/api/status        service health + counters
    GET  /admin/api/meetings      active meetings + participants
    GET  /admin/api/logs          recent log entries (ring buffer)
    GET  /admin/api/config        resolved config tree (read-only)
    POST /admin/api/reset-metrics zero out counters (keeps recent_finals)

Auth: ``X-Admin-Token: <ADMIN_TOKEN>`` header on every API call. The
HTML page itself is unauthenticated but useless without the token —
the dashboard JS reads ``localStorage.casual_admin_token`` and includes
it in every fetch.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from .logs import LOG_BUFFER
from .metrics import METRICS
from .portal import PORTAL_HTML

router = APIRouter(prefix="/admin", tags=["admin"])

#: Set in the environment to enable the portal. Missing/empty → portal
#: returns 404 for every route (effectively disabled).
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()


def _check_admin(token: str | None) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin disabled")
    if token != ADMIN_TOKEN:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad admin token")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def portal() -> HTMLResponse:
    """Serve the HTML dashboard (always served — token gates the data)."""
    if not ADMIN_TOKEN:
        return HTMLResponse(
            "<h1>Admin portal disabled</h1>"
            "<p>Set <code>ADMIN_TOKEN=...</code> in the server's environment "
            "to enable.</p>",
            status_code=404,
        )
    return HTMLResponse(PORTAL_HTML)


@router.get("/api/status")
def api_status(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    return {
        "service": "casual-sst",
        "version": "0.1.0",
        "metrics": METRICS.to_dict(),
    }


@router.get("/api/meetings")
def api_meetings(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    # Imported here to avoid a circular import at module load.
    from .. import main as main_mod

    out: list[dict] = []
    for meeting_id, conn in main_mod._meetings.items():
        participants = []
        for pid, state in conn.participants.items():
            participants.append({
                "participant_id": pid,
                "active_lang": state.lang_state.active_lang,
                "mode": state.lang_state.mode.value,
                "header_lang": state.lang_state.header_lang,
                "is_transcribing": state.is_transcribing,
                "long_silence": state.long_silence,
                "buffer_seconds": round(len(state.working_audio) / 32000, 3),
                "finals_total": state.profile.finals_total,
                "dominant_lang": state.profile.dominant_lang(),
                "locked_strong": state.profile.locked_strong,
                "avg_confidence": round(state.profile.avg_confidence, 3),
            })
        out.append({
            "meeting_id": meeting_id,
            "connected": conn.connected,
            "participants": participants,
        })
    return {"active": out, "count": len(out)}


@router.get("/api/logs")
def api_logs(
    n: int = 200,
    level: str | None = None,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_admin(x_admin_token)
    return {"entries": LOG_BUFFER.recent(n=n, level=level)}


@router.get("/api/config")
def api_config(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    from .. import main as main_mod
    return _sanitise(main_mod.cfg)


@router.post("/api/reset-metrics")
def api_reset_metrics(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    # Reset counters but keep the recent_finals ring (it's the most
    # useful thing to inspect after a wipe).
    METRICS.total_meetings = 0
    METRICS.total_events = 0
    METRICS.total_finals = 0
    METRICS.total_interims = 0
    METRICS.total_language_changes = 0
    METRICS.backend_calls.clear()
    METRICS.backend_total_ms.clear()
    METRICS.backend_errors.clear()
    METRICS.lang_distribution.clear()
    return {"ok": True}


def _sanitise(value: Any) -> Any:
    """Redact anything that looks like a secret before returning to client."""
    SECRETS = ("token", "key", "secret", "password", "credential", "api_key")
    if isinstance(value, dict):
        return {
            k: ("***" if any(s in k.lower() for s in SECRETS) else _sanitise(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitise(v) for v in value]
    return value
