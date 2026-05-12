"""
gemini_websearch.py — Gemini 2.0 Flash via OpenRouter with Google Search grounding.

A single LLM call that searches AND synthesises — no separate SerpAPI step needed.
Used for company enrichment and news lookup in the lead qualifying pipeline.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_MODEL = "google/gemini-2.0-flash-001"

# Regex to extract the first JSON object from a response
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _call_gemini_with_search(
    prompt: str,
    system_prompt: str = "",
    timeout: float = 45.0,
) -> str:
    """
    Call Gemini 2.0 Flash via OpenRouter with Google Search grounding enabled.

    Returns the model's text response. Raises on HTTP / network errors so callers
    can wrap this in a try/except and fall back gracefully.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": GEMINI_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
        # Enable Google Search grounding for Gemini via OpenRouter
        "tools": [{"googleSearch": {}}],
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/serbskisyn/Serbo_bot",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        # Some Gemini responses return content as a list of parts
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content or ""


async def enrich_contact(vorname: str, nachname: str, firma: str) -> dict:
    """
    Research a contact person using Gemini + Google Search.

    Returns a dict with keys:
      contact_title, linkedin_url, confidence
    """
    name = f"{vorname} {nachname}".strip()
    prompt = f"""Recherchiere die Person "{name}", die bei "{firma}" arbeitet.

Finde:
1. Aktuelle Berufsbezeichnung / Position (z.B. "Head of Marketing", "Geschäftsführer", "E-Commerce Manager")
2. LinkedIn-Profil-URL falls öffentlich auffindbar

Antworte NUR mit validem JSON, kein Text davor oder danach:
{{
  "contact_title": "Berufsbezeichnung oder leer",
  "linkedin_url": "https://linkedin.com/in/... oder leer",
  "confidence": "high|medium|low"
}}"""

    system = (
        "Du bist ein B2B-Researcher. Nutze deine Web-Suche um aktuelle Informationen "
        "zu finden. Antworte ausschließlich mit dem angeforderten JSON."
    )

    try:
        raw = await _call_gemini_with_search(prompt, system_prompt=system)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info(
                "gemini_websearch.enrich_contact: '%s' → title='%s' confidence=%s",
                name, data.get("contact_title"), data.get("confidence"),
            )
            return data
        logger.warning("gemini_websearch.enrich_contact: kein JSON in Antwort für '%s'", name)
    except httpx.HTTPStatusError as exc:
        logger.warning("gemini_websearch enrich_contact HTTP %s für '%s'", exc.response.status_code, name)
    except httpx.TimeoutException:
        logger.warning("gemini_websearch enrich_contact Timeout für '%s'", name)
    except json.JSONDecodeError as exc:
        logger.warning("gemini_websearch enrich_contact JSON-Fehler für '%s': %s", name, exc)
    except Exception as exc:
        logger.warning("gemini_websearch enrich_contact Fehler für '%s': %s", name, exc)

    return {"contact_title": "", "linkedin_url": "", "confidence": "low"}


async def enrich_company(firma: str) -> dict:
    """
    Research a company using Gemini + Google Search and return structured data.

    Returns a dict with keys:
      company_website, company_description, industry, employee_count_estimate,
      northdata_hint (any financial/legal signals found), confidence
    """
    prompt = f"""Recherchiere das Unternehmen "{firma}" im deutschen Markt.

Finde und extrahiere folgende Informationen:
1. Offizielle Website-URL
2. Kurze Beschreibung was das Unternehmen macht (max. 2 Sätze)
3. Branche/Sektor
4. Schätzung der Mitarbeiterzahl
5. Hinweise auf: E-Commerce-Aktivitäten, Affiliate/Cashback/Deal-Marketing, Online-Shop

Antworte NUR mit validem JSON, kein Text davor oder danach:
{{
  "company_website": "URL oder leer",
  "company_description": "2 Sätze Beschreibung",
  "industry": "Branche",
  "employee_count_estimate": "z.B. 10-50 oder >1000",
  "ecommerce_signals": "Kurze Beschreibung Online-/Affiliate-Aktivitäten oder 'keine gefunden'",
  "confidence": "high|medium|low"
}}"""

    system = (
        "Du bist ein B2B-Researcher. Nutze deine Web-Suche um aktuelle Informationen "
        "zu finden. Antworte ausschließlich mit dem angeforderten JSON."
    )

    try:
        raw = await _call_gemini_with_search(prompt, system_prompt=system)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info("gemini_websearch.enrich_company: '%s' → confidence=%s", firma, data.get("confidence"))
            return data
        logger.warning("gemini_websearch.enrich_company: kein JSON in Antwort für '%s'", firma)
    except httpx.HTTPStatusError as exc:
        logger.warning("gemini_websearch HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("gemini_websearch Timeout für '%s'", firma)
    except json.JSONDecodeError as exc:
        logger.warning("gemini_websearch JSON-Parse-Fehler für '%s': %s", firma, exc)
    except Exception as exc:
        logger.warning("gemini_websearch unbekannter Fehler für '%s': %s", firma, exc)

    return {
        "company_website": "",
        "company_description": "",
        "industry": "",
        "employee_count_estimate": "",
        "ecommerce_signals": "",
        "confidence": "low",
    }


async def get_news_summary(firma: str) -> str:
    """
    Research recent news about a company and return a 2-3 sentence German summary.

    Focuses on: growth, funding, partnerships, affiliate/performance-marketing signals,
    D2C launches, international expansion.
    """
    prompt = f"""Suche nach aktuellen Nachrichten (2024-2025) über das Unternehmen "{firma}".

Fokus auf:
- Finanzierungsrunden, Wachstum, Expansion
- Partnerschaften, Kooperationen
- E-Commerce, Affiliate-Marketing, Performance-Marketing, D2C
- Internationalisierung
- Relevante Änderungen im Geschäftsmodell

Fasse die wichtigsten Erkenntnisse in 2-3 prägnanten deutschen Sätzen zusammen.
Falls keine relevanten Nachrichten gefunden: Antworte mit "Keine aktuellen Nachrichten gefunden."
Nur die Zusammenfassung, kein JSON, kein Titel."""

    system = (
        "Du bist ein B2B-Researcher. Nutze deine Web-Suche für aktuelle Informationen. "
        "Antworte auf Deutsch, prägnant und faktenbasiert."
    )

    try:
        summary = await _call_gemini_with_search(prompt, system_prompt=system)
        summary = summary.strip()
        logger.info("gemini_websearch.get_news_summary: '%s' → %d Zeichen", firma, len(summary))
        return summary or "Keine aktuellen Nachrichten gefunden."
    except httpx.HTTPStatusError as exc:
        logger.warning("gemini_websearch news HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("gemini_websearch news Timeout für '%s'", firma)
    except Exception as exc:
        logger.warning("gemini_websearch news Fehler für '%s': %s", firma, exc)

    return "Keine aktuellen Nachrichten gefunden."
