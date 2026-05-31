"""
LLM backend abstraction.

Two implementations:

  * :class:`OpenAILLM` — HTTP POST to any OpenAI-format ``/chat/completions``
    endpoint. Covers OpenAI itself, LM Studio, llama.cpp's `--api-server`,
    vLLM's `--api-server-cli`, Groq, Fireworks, Together, etc.
  * :class:`OllamaLLM` — HTTP POST to Ollama's native ``/api/chat``.

Both use ``httpx`` (already pulled in transitively by ``fastapi``); we
do **not** import the official ``openai`` / ``ollama`` SDKs — keeps
the runtime image smaller and the API surface explicit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx


class LLM(Protocol):
    """Minimal LLM interface — chat-completion-style."""

    async def chat(self, system: str, user: str) -> str: ...


@dataclass
class OpenAILLM:
    """Generic OpenAI-format chat endpoint."""
    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0

    async def chat(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]


@dataclass
class OllamaLLM:
    """Ollama native chat API."""
    host: str
    model: str
    timeout: float = 120.0   # Local models can be slow on first prompt.

    async def chat(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.host.rstrip('/')}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
            )
            r.raise_for_status()
            data = r.json()
            return data["message"]["content"]


def select_llm() -> LLM | None:
    """Resolve the LLM backend from environment. Returns None if disabled.

    Selection order: ``LLM_BACKEND`` env var picks one of "openai" /
    "ollama". Anything else (including empty) disables the module.
    """
    choice = os.environ.get("LLM_BACKEND", "").strip().lower()
    if choice == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None  # configured but no key → behave as disabled.
        return OpenAILLM(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=api_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
    if choice == "ollama":
        return OllamaLLM(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        )
    return None
