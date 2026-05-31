"""
casual_sst.health
=================

Liveness + readiness endpoint.

Two routes:

  GET /healthz    — liveness. Returns 200 as long as the process is
                    running. Used by container orchestrators to decide
                    "is this PID alive?". Cheap; no auth.

  GET /health     — readiness. Returns 200 only when the router has
                    instantiated every backend referenced by `routes:`
                    AND at least one backend is callable. Returns 503
                    otherwise. This is what compose.prod-cuda.yaml's
                    healthcheck hits; it's stricter than the old
                    "TCP-accept on port 8000" probe.

Both endpoints are PII-free and unauthenticated by design — they need
to be reachable by load balancers / orchestrators that don't carry
admin tokens.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", include_in_schema=False)
def liveness() -> dict[str, Any]:
    """Process is alive. Always 200 — useful for k8s liveness probes."""
    return {"ok": True, "service": "casual-sst"}


@router.get("/health")
def readiness() -> JSONResponse:
    """Service is ready to serve transcription requests.

    Checks:
      * the global Router has loaded its backends
      * each instantiated backend has a `transcribe` (chunked) or
        `feed` (native streaming) method — i.e. it's not a stub
      * the in-memory metrics singleton is initialised
    """
    # Imported here so health module is importable in unit tests
    # without standing up the full FastAPI app.
    from . import main as main_mod
    from .admin.metrics import METRICS

    try:
        router_obj = main_mod.router
    except AttributeError:
        return _not_ready("router not loaded yet")

    backends = getattr(router_obj, "_backends", {}) or {}
    if not backends:
        return _not_ready("no backends loaded")

    backend_status: dict[str, str] = {}
    all_ok = True
    for name, binding in backends.items():
        inst = binding.instance
        # ChunkedASR vs NativeStreamingASR — either method-set is fine.
        is_chunked = callable(getattr(inst, "transcribe", None))
        is_native = callable(getattr(inst, "feed", None)) and callable(
            getattr(inst, "open_stream", None)
        )
        if is_chunked or is_native:
            backend_status[name] = "ok"
        else:
            backend_status[name] = "stub or broken"
            all_ok = False

    payload = {
        "ok": all_ok,
        "service": "casual-sst",
        "backends": backend_status,
        "uptime_seconds": int(_uptime_s()),
        "total_meetings": METRICS.total_meetings,
        "total_finals": METRICS.total_finals,
    }
    return JSONResponse(payload, status_code=200 if all_ok else 503)


def _not_ready(reason: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "service": "casual-sst", "reason": reason},
        status_code=503,
    )


def _uptime_s() -> float:
    import time
    from .admin.metrics import METRICS
    return time.time() - METRICS.started_at
