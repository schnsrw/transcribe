"""
HTTP surface for the optional LLM module.

Mounted at ``/api/*``. Returns 503 when no LLM backend is configured
in the environment, so misconfigured stacks fail loudly + the docs
endpoint shows the route either way.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .backends import select_llm
from .ratelimit import LIMITER

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["llm"])

SUMMARY_SYSTEM = (
    "You are a meeting-summary assistant. Given a raw transcript, "
    "produce a JSON object with two keys:\n"
    "  summary       — 3-6 sentences capturing the key discussion.\n"
    "  action_items  — array of strings, each a single concrete action.\n"
    "Reply with ONLY the JSON object. No markdown fences. No prose."
)


class SummarizeRequest(BaseModel):
    transcript: str = Field(..., min_length=10, description="Finalized transcript text.")
    language_hint: str | None = Field(
        None, description="ISO lang code to bias the LLM (e.g. 'en', 'hi')."
    )


class SummarizeResponse(BaseModel):
    summary: str
    action_items: list[str]


@router.post("/summarize", response_model=SummarizeResponse)
async def summarize(req: SummarizeRequest, request: Request) -> SummarizeResponse:
    """Summarize a transcript into bullet points + action items.

    Returns 503 if no LLM backend is configured (LLM_BACKEND env var
    unset or pointed at a backend whose credentials are missing).
    Returns 429 if the caller's IP has exceeded the per-IP rate limit
    (default 20 req/min, configurable via LLM_RATE_LIMIT_RPM env).
    """
    # Per-IP token bucket. Behind a proxy this is the proxy IP — make
    # sure you trust X-Forwarded-For before relying on it here.
    client_ip = (request.client.host if request.client else "unknown")
    if not LIMITER.allow(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded. Tune via LLM_RATE_LIMIT_RPM / "
            "LLM_RATE_LIMIT_BURST env vars or put a proper API gateway "
            "in front of this endpoint.",
        )

    llm = select_llm()
    if llm is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LLM backend not configured — set LLM_BACKEND in the env "
            "(see .env.example).",
        )

    lang_hint = f"\n\nLanguage of the transcript: {req.language_hint}." if req.language_hint else ""
    user_msg = f"Transcript:\n{req.transcript}{lang_hint}"

    try:
        raw = await llm.chat(SUMMARY_SYSTEM, user_msg)
    except Exception as e:
        log.exception("LLM chat failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM error: {e!r}")

    # Strip common LLM noise — the system prompt says "ONLY JSON" but
    # some models still wrap in ```json fences. Be forgiving.
    cleaned = raw.strip().lstrip("`").rstrip("`").strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].lstrip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to wrapping the raw response as a summary so the
        # client at least gets something useful.
        log.warning("LLM returned non-JSON; wrapping verbatim")
        return SummarizeResponse(summary=raw.strip(), action_items=[])

    return SummarizeResponse(
        summary=str(parsed.get("summary", "")).strip(),
        action_items=[str(x).strip() for x in (parsed.get("action_items") or []) if str(x).strip()],
    )


@router.get("/summarize/health")
def summarize_health() -> dict:
    """Quick health probe — does NOT call the LLM, just reports config."""
    llm = select_llm()
    return {
        "configured": llm is not None,
        "backend": type(llm).__name__ if llm else None,
    }
