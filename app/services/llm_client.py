"""
llm_client.py — central LiteLLM (OpenAI-compatible) client.

Replaces the scattered direct-to-OpenRouter httpx calls. One place for the
base URL + key + model aliases, so the whole bot routes through the Atolls
LiteLLM proxy. Web-search enrichment uses Gemini's googleSearch grounding
(chat_grounded) — Anthropic web_search via Vertex is blocked on the proxy.

Model aliases (config): LLM_CHEAP_MODEL, LLM_STRONG_MODEL, LLM_GROUNDED_MODEL.
"""
from __future__ import annotations

import logging

import httpx

from app.config import LITELLM_API_KEY, LITELLM_BASE_URL, LLM_GROUNDED_MODEL

logger = logging.getLogger(__name__)


def _url() -> str:
    return f"{LITELLM_BASE_URL.rstrip('/')}/chat/completions"


async def chat(
    messages: list[dict],
    *,
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 600,
    tools: list[dict] | None = None,
    timeout: float = 30.0,
) -> str:
    """Single LiteLLM chat completion. Returns the assistant content string.
    Raises httpx errors so callers keep their existing try/except handling."""
    if not LITELLM_BASE_URL or not LITELLM_API_KEY:
        raise RuntimeError("LiteLLM nicht konfiguriert (LITELLM_BASE_URL/API_KEY fehlt)")
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(_url(), json=payload, headers=headers)
        # Some models (e.g. Claude Opus 4.8 on Vertex) reject the temperature
        # param ("temperature is deprecated for this model"). Retry without it.
        if r.status_code == 400 and "temperature" in r.text.lower():
            payload.pop("temperature", None)
            r = await client.post(_url(), json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return (data["choices"][0]["message"].get("content") or "")


async def chat_grounded(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 900,
    timeout: float = 60.0,
) -> str:
    """Chat with Gemini Google-Search grounding (live web research)."""
    return await chat(
        messages,
        model=model or LLM_GROUNDED_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=[{"googleSearch": {}}],
        timeout=timeout,
    )
