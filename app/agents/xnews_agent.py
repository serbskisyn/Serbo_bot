"""
X-News Agent — fragt Grok mit Live-X-Search nach einem Thema
und formatiert die Antwort für Telegram (Markdown).
"""
from __future__ import annotations

import logging

from app.services.grok_client import grok_search

logger = logging.getLogger(__name__)

MAX_CITATIONS_SHOWN = 8


async def fetch_x_news(topic: str) -> str:
    """
    Returns Markdown-Text mit Grok-Zusammenfassung + Quellen-Liste.
    Topic darf leer/None nicht sein.
    """
    topic = (topic or "").strip()
    if not topic:
        return "ℹ️ Bitte ein Thema angeben: `/xnews <thema>`"

    logger.info("[xnews] query=%r", topic)
    result = await grok_search(
        query=f"Was sind die aktuellen X-Posts zum Thema: {topic}?",
        sources=["x"],
        max_results=10,
    )

    text      = result["text"]
    citations = result["citations"]
    model     = result["model"]

    parts = [f"🔎 *X-News:* _{_escape_md(topic)}_", "", text]
    if citations:
        parts.append("")
        parts.append("*Quellen:*")
        for i, url in enumerate(citations[:MAX_CITATIONS_SHOWN], 1):
            parts.append(f"{i}. {url}")
        if len(citations) > MAX_CITATIONS_SHOWN:
            parts.append(f"…und {len(citations) - MAX_CITATIONS_SHOWN} weitere.")
    parts.append("")
    parts.append(f"_Modell: {model}_")
    return "\n".join(parts)


def _escape_md(text: str) -> str:
    """Minimale Markdown-Eskapierung — nur Zeichen, die das Italic-Markup brechen."""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
