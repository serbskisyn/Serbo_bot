"""
perplexity_websearch.py — Perplexity Sonar via OpenRouter mit eingebauter Web-Suche.

Drop-in-Ersatz für gemini_websearch.py: gleiche Funktionssignaturen, gleiche
Output-Dicts, anderer Anbieter. Perplexity hat Live-Web-Suche eingebaut — keine
extra Tools-Konfiguration nötig.

Model wird via PERPLEXITY_MODEL env-var gesteuert (Default: perplexity/sonar-pro).
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from app.config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "perplexity/sonar-pro")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _call_perplexity(
    prompt: str,
    system_prompt: str = "",
    timeout: float = 45.0,
) -> str:
    """Ein OpenRouter-Call gegen Perplexity Sonar. Suche ist eingebaut, keine extra Tools.

    Wirft auf HTTP/Netzwerk-Fehlern, damit die Caller sauber abfangen können.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1024,
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
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content or ""


async def enrich_contact(vorname: str, nachname: str, firma: str) -> dict:
    """B2B-Recherche zu einer Kontaktperson. Drop-in-Replacement für gemini-Variante."""
    name = f"{vorname} {nachname}".strip()
    prompt = f"""Recherchiere die Person "{name}", die bei "{firma}" arbeitet.

Finde:
1. Aktuelle Berufsbezeichnung / Position (z.B. "Head of Marketing", "Geschäftsführer", "E-Commerce Manager")
2. LinkedIn-Profil-URL falls öffentlich auffindbar

Antworte AUSSCHLIESSLICH mit validem JSON, kein Text davor oder danach, keine Markdown-Fences:
{{
  "contact_title": "Berufsbezeichnung oder leer",
  "linkedin_url": "https://linkedin.com/in/... oder leer",
  "confidence": "high|medium|low"
}}"""

    system = (
        "Du bist ein B2B-Researcher mit Live-Web-Suche. "
        "Antworte ausschließlich mit dem angeforderten JSON-Objekt — kein erklärender Text."
    )

    try:
        raw = await _call_perplexity(prompt, system_prompt=system)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info(
                "perplexity_websearch.enrich_contact: '%s' → title='%s' confidence=%s",
                name, data.get("contact_title"), data.get("confidence"),
            )
            return data
        logger.warning("perplexity_websearch.enrich_contact: kein JSON in Antwort für '%s'", name)
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity enrich_contact HTTP %s für '%s'", exc.response.status_code, name)
    except httpx.TimeoutException:
        logger.warning("perplexity enrich_contact Timeout für '%s'", name)
    except json.JSONDecodeError as exc:
        logger.warning("perplexity enrich_contact JSON-Fehler für '%s': %s", name, exc)
    except Exception as exc:
        logger.warning("perplexity enrich_contact Fehler für '%s': %s", name, exc)

    return {"contact_title": "", "linkedin_url": "", "confidence": "low"}


async def enrich_company(firma: str) -> dict:
    """Firmen-Recherche inkl. Unternehmensgröße, Branche, E-Commerce-Signale."""
    prompt = f"""Recherchiere das Unternehmen "{firma}" — Schwerpunkt deutscher und europäischer Markt.

Finde und extrahiere folgende Informationen aus aktuellen Quellen (2024-2026):
1. Offizielle Website-URL
2. Kurze Beschreibung was das Unternehmen macht (max. 2 Sätze)
3. Branche/Sektor
4. Schätzung der Mitarbeiterzahl (z.B. "1-10", "10-50", "50-200", "200-1000", ">1000")
5. Hinweise auf: E-Commerce-Aktivitäten, Affiliate/Cashback/Deal-Marketing, Online-Shop, D2C

Antworte AUSSCHLIESSLICH mit validem JSON, kein Text davor/danach, keine Markdown-Fences:
{{
  "company_website": "URL oder leer",
  "company_description": "2 Sätze Beschreibung",
  "industry": "Branche",
  "employee_count_estimate": "z.B. 10-50 oder >1000",
  "ecommerce_signals": "Kurze Beschreibung Online-/Affiliate-Aktivitäten oder 'keine gefunden'",
  "confidence": "high|medium|low"
}}"""

    system = (
        "Du bist ein B2B-Researcher mit Live-Web-Suche. "
        "Nutze aktuelle Quellen. Antworte ausschließlich mit dem angeforderten JSON-Objekt."
    )

    try:
        raw = await _call_perplexity(prompt, system_prompt=system)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info(
                "perplexity_websearch.enrich_company: '%s' → size='%s' confidence=%s",
                firma, data.get("employee_count_estimate"), data.get("confidence"),
            )
            return data
        logger.warning("perplexity_websearch.enrich_company: kein JSON für '%s'", firma)
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("perplexity Timeout für '%s'", firma)
    except json.JSONDecodeError as exc:
        logger.warning("perplexity JSON-Parse-Fehler für '%s': %s", firma, exc)
    except Exception as exc:
        logger.warning("perplexity unbekannter Fehler für '%s': %s", firma, exc)

    return {
        "company_website": "",
        "company_description": "",
        "industry": "",
        "employee_count_estimate": "",
        "ecommerce_signals": "",
        "confidence": "low",
    }


async def get_news_summary(firma: str) -> str:
    """Aktuelle News-Zusammenfassung in 2-3 deutschen Sätzen."""
    prompt = f"""Suche nach aktuellen Nachrichten (2024-2026) über das Unternehmen "{firma}".

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
        "Du bist ein B2B-Researcher mit Live-Web-Suche. "
        "Antworte auf Deutsch, prägnant und faktenbasiert."
    )

    try:
        summary = await _call_perplexity(prompt, system_prompt=system)
        summary = summary.strip()
        logger.info("perplexity_websearch.get_news_summary: '%s' → %d Zeichen", firma, len(summary))
        return summary or "Keine aktuellen Nachrichten gefunden."
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity news HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("perplexity news Timeout für '%s'", firma)
    except Exception as exc:
        logger.warning("perplexity news Fehler für '%s': %s", firma, exc)

    return "Keine aktuellen Nachrichten gefunden."
