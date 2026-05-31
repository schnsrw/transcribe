"""
casual_sst.llm
==============

Optional LLM module — provides ``POST /api/summarize`` for turning a
finalized transcript into a summary + action items.

**Off by default.** Enabled when ``LLM_BACKEND`` is set in the env to
one of:

  * ``openai``  — uses ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL``
                  (default ``https://api.openai.com/v1``), ``OPENAI_MODEL``.
                  Compatible with any "OpenAI-format" server (LM Studio,
                  vLLM with the openai-compat shim, llama.cpp's server,
                  Together, Groq, Fireworks, etc.) — point ``OPENAI_BASE_URL``
                  at it.
  * ``ollama``  — uses ``OLLAMA_HOST`` (default ``http://localhost:11434``)
                  + ``OLLAMA_MODEL``.

The endpoint is intentionally generic: send `{"transcript": "..."}` and
get back `{"summary": "...", "action_items": [...]}`. No per-meeting
state, no streaming — keep it stateless and easy to put behind any
LLM. For Skynet-style "summarise meeting M for participant P" flows,
build that on top of this endpoint + your own state store.
"""

from .api import router as llm_router  # noqa: F401
