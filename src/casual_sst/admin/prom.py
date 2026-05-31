"""
Prometheus exposition for the in-process METRICS counters.

We don't pull in ``prometheus_client`` — the surface area is small and
keeping the runtime image lean matters. Format follows the text-based
Prometheus exposition format (v0.0.4); any Prometheus / OpenMetrics
scraper will parse it.

Endpoint: ``GET /metrics`` — always on, unauthenticated. If you don't
want it public, put an auth-aware proxy in front (caddy / nginx) and
only allow your monitoring subnet. Counters do not contain PII —
event-text and participant IDs stay in /admin/api/* under
``X-Admin-Token``.
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .metrics import METRICS

router = APIRouter(tags=["metrics"])


def _render(name: str, type_: str, help_: str, value: float | int,
            labels: dict[str, str] | None = None) -> str:
    lbl = ""
    if labels:
        lbl = "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items()) + "}"
    return (
        f"# HELP {name} {help_}\n"
        f"# TYPE {name} {type_}\n"
        f"{name}{lbl} {value}\n"
    )


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def prometheus_metrics() -> PlainTextResponse:
    """Render the in-process counters in Prometheus exposition format."""
    out: list[str] = []

    out.append(_render(
        "casual_sst_uptime_seconds", "counter",
        "Seconds since the service started.",
        int(time.time() - METRICS.started_at),
    ))
    out.append(_render(
        "casual_sst_meetings_total", "counter",
        "Total number of WebSocket meeting connections accepted.",
        METRICS.total_meetings,
    ))
    out.append(_render(
        "casual_sst_events_total", "counter",
        "Total transcription events emitted (interim + final).",
        METRICS.total_events,
    ))
    out.append(_render(
        "casual_sst_finals_total", "counter",
        "Total final transcription events emitted.",
        METRICS.total_finals,
    ))
    out.append(_render(
        "casual_sst_interims_total", "counter",
        "Total interim transcription events emitted.",
        METRICS.total_interims,
    ))
    out.append(_render(
        "casual_sst_language_changes_total", "counter",
        "Total mid-call language-change events emitted (LID-driven).",
        METRICS.total_language_changes,
    ))

    # Per-backend metrics
    for name, calls in METRICS.backend_calls.items():
        out.append(_render(
            "casual_sst_backend_calls_total", "counter",
            "Total transcribe() calls per backend.",
            calls, {"backend": name},
        ))
    for name, total_ms in METRICS.backend_total_ms.items():
        out.append(_render(
            "casual_sst_backend_time_ms_total", "counter",
            "Cumulative time spent in transcribe() per backend (ms).",
            int(total_ms), {"backend": name},
        ))
    for name, errors in METRICS.backend_errors.items():
        out.append(_render(
            "casual_sst_backend_errors_total", "counter",
            "Total transcribe() errors per backend.",
            errors, {"backend": name},
        ))

    # Per-language event counts
    for lang, count in METRICS.lang_distribution.items():
        out.append(_render(
            "casual_sst_finals_by_language_total", "counter",
            "Total final events broken down by detected/active language.",
            count, {"language": lang or "unknown"},
        ))

    return PlainTextResponse("".join(out), media_type="text/plain; version=0.0.4")
