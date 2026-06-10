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
    max_tokens: int = 1024,
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
        "max_tokens": max_tokens,
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


async def enrich_contact(vorname: str, nachname: str, firma: str,
                         email: str = "", context: str = "") -> dict:
    """B2B contact research with role-match + decision authority. Output in English.

    Uses the lead's email (domain confirms the company) and their inbound message
    (people often describe their own role/seniority there) so the role can be
    inferred even when web search can't pin the person down.
    """
    name = f"{vorname} {nachname}".strip()
    email_line = (f'\nTheir email: "{email}" — the domain confirms the company, '
                  f'the local part may hint at the person.') if email else ""
    context_line = (
        f'\nThey wrote this in their inbound partnership request — use it to infer '
        f'title/seniority/authority when web search is inconclusive (people often '
        f'state their own role here):\n"""{context[:600]}"""'
    ) if context.strip() else ""
    prompt = f"""Research the person "{name}" who works at "{firma}".{email_line}{context_line}

Search LinkedIn and the company website for this person.

Find:
1. Current job title / position (e.g. "Head of Marketing", "CEO", "E-Commerce Manager")
2. Public LinkedIn profile URL if available
3. Whether this person is likely a decision-maker for affiliate/cashback/coupon partnerships
4. Whether the role is relevant for Atolls' platforms (Shoop cashback, iGraal cashback/coupons, mydealz deal-community)

If you cannot verify the person online, INFER title/authority/role_match from the
inbound message above instead of leaving everything empty (set confidence=low when inferred).

Reply ONLY with valid JSON, no surrounding text, no markdown fences:
{{
  "contact_title": "Job title in English (translate if necessary), or empty string",
  "linkedin_url": "https://linkedin.com/in/... or empty",
  "authority": "decision_maker | influencer | other",
  "role_match": true|false,
  "confidence": "high|medium|low"
}}

authority guidance:
- decision_maker = C-level, Founder, Director, Head of, VP — can sign partnership deals
- influencer = Manager, Lead, Senior Specialist — influences but doesn't sign
- other = Junior, Specialist, unclear, unknown

role_match = true if the role is in Marketing / E-Commerce / Performance Marketing / Partnerships / Affiliate / Sales / Digital / Growth.
role_match = false for IT/Tech/Operations/HR/Finance/Legal/Customer Service unless explicitly tied to performance marketing."""

    system = (
        "You are a B2B contact researcher with live web search. "
        "Reply ONLY with the requested JSON object — no surrounding prose."
    )

    try:
        raw = await _call_perplexity(prompt, system_prompt=system)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info(
                "perplexity_websearch.enrich_contact: '%s' → title='%s' auth=%s role_match=%s",
                name, data.get("contact_title"), data.get("authority"), data.get("role_match"),
            )
            return data
        logger.warning("perplexity_websearch.enrich_contact: no JSON returned for '%s'", name)
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity enrich_contact HTTP %s for '%s'", exc.response.status_code, name)
    except httpx.TimeoutException:
        logger.warning("perplexity enrich_contact timeout for '%s'", name)
    except json.JSONDecodeError as exc:
        logger.warning("perplexity enrich_contact JSON error for '%s': %s", name, exc)
    except Exception as exc:
        logger.warning("perplexity enrich_contact error for '%s': %s", name, exc)

    return {"contact_title": "", "linkedin_url": "", "authority": "other",
            "role_match": False, "confidence": "low"}


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


async def discover_ecommerce_brands(firma: str) -> dict:
    """Findet die eCommerce-nahen Marken, die ein Unternehmen besitzt/betreibt/verantwortet.

    Output:
      {
        "brands": [
          {"name": "Temu", "domain": "temu.com", "category": "Marketplace",
           "rationale": "Hauptmarke des Unternehmens"},
          ...
        ],
        "confidence": "high|medium|low",
        "is_holding": bool   # True wenn Holding/Group mit mehreren eCom-Marken
      }
    """
    prompt = f"""Research the company "{firma}":

Which eCommerce-related brands / shops / online platforms does this company operate, own, or run?

Consider:
- Own online shops (e.g. www.company.com)
- Subsidiary brands and sub-brands
- White-label / private-label brands
- Marketplaces where the company sells
- D2C concepts, cashback / coupon activities

If the company is a holding: list at most 12 most-relevant eCommerce subsidiaries (prioritise by visibility / revenue).
If the company is B2B-only / has no eCom relevance: set brands=[] and is_holding=false.

Keep "rationale" VERY SHORT (max 8 words in English) so the JSON stays compact.

Reply ONLY with valid JSON, no surrounding text, no markdown fences:
{{
  "brands": [
    {{"name": "Brand Name", "domain": "domain.com or empty", "category": "Marketplace|Online-Shop|Marketplace-Seller|D2C|White-Label|Affiliate|Other", "rationale": "max 8 English words"}}
  ],
  "is_holding": true|false,
  "confidence": "high|medium|low"
}}"""

    system = (
        "You are a B2B researcher with live web search, specialised in "
        "eCommerce brand discovery. Reply ONLY with the requested JSON. "
        "Always respond in English, regardless of the company's home language."
    )

    try:
        raw = await _call_perplexity(prompt, system_prompt=system, timeout=60.0, max_tokens=2048)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            brands = data.get("brands") or []
            # Filtern: keine leeren Namen
            brands = [b for b in brands if isinstance(b, dict) and b.get("name", "").strip()]
            logger.info(
                "perplexity.discover_ecommerce_brands: '%s' → %d Marken | confidence=%s | holding=%s",
                firma, len(brands), data.get("confidence"), data.get("is_holding"),
            )
            return {
                "brands":     brands,
                "is_holding": bool(data.get("is_holding", False)),
                "confidence": data.get("confidence", "low"),
            }
        logger.warning("perplexity.discover_ecommerce_brands: kein JSON für '%s'", firma)
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity discover HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("perplexity discover Timeout für '%s'", firma)
    except json.JSONDecodeError as exc:
        logger.warning("perplexity discover JSON-Parse-Fehler für '%s': %s", firma, exc)
    except Exception as exc:
        logger.warning("perplexity discover unbekannter Fehler für '%s': %s", firma, exc)

    return {"brands": [], "is_holding": False, "confidence": "low"}


async def validate_company_sales(firma: str, brands: list[dict]) -> dict:
    """Validiert die Brand-Liste + holt Sales-relevante Firmenfakten.

    Output:
      {
        "validated_brands": [...],   # vom LLM bestätigte Subset von Input-brands
        "revenue_estimate": "z.B. 50-100M EUR",
        "employee_count": "z.B. 200-500",
        "headquarters": "z.B. Köln, DE",
        "primary_markets": ["DE", "AT", "CH"],
        "business_model": "B2C/B2B/Marketplace/Hybrid",
        "sales_signals": "1-3 Sätze relevante Verkaufs-Signale (Wachstum, Kampagnen, etc.)",
        "confidence": "high|medium|low"
      }
    """
    brand_list_text = ", ".join(b.get("name", "") for b in brands) or "(keine bekannt)"
    brand_block = "\n".join(
        f"- {b.get('name', '')} ({b.get('category', '')}, {b.get('domain', 'kein Domain')})"
        for b in brands
    ) or "(keine eCommerce-Marken identifiziert)"

    prompt = f"""Research and validate the company "{firma}" for B2B sales qualification.

Previously identified eCommerce brands (not all may be accurate):
{brand_block}

Please verify and enrich:

1. **Brand validation**: Which of the brands above demonstrably belong to the company?
   Remove false matches, add any that were missed.

2. **Company facts** for sales context:
   - Revenue estimate (e.g. "<10M EUR", "10-50M EUR", "50-200M EUR", "200M-1B EUR", ">1B EUR")
   - Employee count
   - Headquarters / country code
   - Primary markets (max 5 ISO codes)
   - Business model (B2C / B2B / Marketplace / Hybrid / Manufacturer-Direct)

3. **Sales signals**: 2-3 sentences relevant for cashback / affiliate / deal partnerships:
   growth, funding, expansion, performance-marketing needs, existing affiliate programs.

Reply ONLY with valid JSON, no surrounding text:
{{
  "validated_brands": [
    {{"name": "Brand Name", "domain": "domain.com", "category": "...", "rationale": "max 8 English words"}}
  ],
  "revenue_estimate": "e.g. 50-200M EUR or unknown",
  "employee_count": "e.g. 200-500 or unknown",
  "headquarters": "e.g. Cologne, DE or empty",
  "primary_markets": ["DE", "AT", "CH"],
  "business_model": "B2C|B2B|Marketplace|Hybrid|Manufacturer-Direct|Unknown",
  "sales_signals": "2-3 sentences in English on growth/funding/expansion/affiliate needs",
  "confidence": "high|medium|low"
}}"""

    system = (
        "You are a B2B sales researcher with live web search. "
        "Reply ONLY with the requested JSON, in English. "
        "Use 'unknown' for fields that cannot be researched."
    )

    try:
        raw = await _call_perplexity(prompt, system_prompt=system, timeout=60.0, max_tokens=2048)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info(
                "perplexity.validate_company_sales: '%s' → revenue=%s employees=%s confidence=%s",
                firma, data.get("revenue_estimate"), data.get("employee_count"), data.get("confidence"),
            )
            return data
        logger.warning("perplexity.validate_company_sales: kein JSON für '%s'", firma)
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity validate HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("perplexity validate Timeout für '%s'", firma)
    except json.JSONDecodeError as exc:
        logger.warning("perplexity validate JSON-Parse-Fehler für '%s': %s", firma, exc)
    except Exception as exc:
        logger.warning("perplexity validate unbekannter Fehler für '%s': %s", firma, exc)

    return {
        "validated_brands": brands,
        "revenue_estimate": "unknown",
        "employee_count":   "unknown",
        "headquarters":     "",
        "primary_markets":  [],
        "business_model":   "Unknown",
        "sales_signals":    "",
        "confidence":       "low",
    }


async def enrich_commercial_intelligence(
    firma: str,
    brands: list[dict],
    business_model: str,
    primary_markets: list[str],
    revenue_estimate: str,
) -> dict:
    """Research performance marketing maturity, affiliate signals, and promo behavior.

    Output:
      {
        "marketing_spend_estimate": "~2-5M EUR/year or unknown",
        "google_shopping_presence": "high|medium|low|none",
        "meta_tiktok_activity": "high|medium|low|none",
        "amazon_ads_presence": "high|medium|low|none",
        "affiliate_networks": ["AWIN", "CJ"],
        "coupon_promo_frequency": "frequent|occasional|rare|none",
        "bf_prime_day_signals": true|false,
        "deal_community_presence": "active|occasional|none",
        "cashback_platform_presence": ["Shoop", "iGraal"],
        "perf_mktg_sophistication": "high|medium|low",
        "affiliate_likelihood": "high|medium|low",
        "promo_intensity_summary": "2 sentences",
        "confidence": "high|medium|low"
      }
    """
    brand_text = ", ".join(b.get("name", "") for b in brands if b.get("name")) or "(none identified)"
    markets_text = ", ".join(primary_markets) if primary_markets else "unknown"

    prompt = f"""Research the performance marketing maturity and affiliate signals for "{firma}"
(eCommerce brands: {brand_text}, business model: {business_model},
revenue: {revenue_estimate}, primary markets: {markets_text}).

Investigate:
1. **Marketing spend** — triangulate from headcount, revenue scale, visible ad activity.
   Give a rough EUR/year estimate (e.g. "~2-5M EUR/year" or "unknown").
2. **Performance channels** — is the brand running Google Shopping (product listing ads)?
   Active on Meta Ads / Instagram / TikTok Ads? Using Amazon Sponsored Products?
3. **Affiliate & deal activity** — affiliate network memberships (AWIN, CJ, Tradedoubler,
   Webgains, etc.), coupon code distribution (RetailMeNot, Gutscheinpanda, honey, etc.),
   Black Friday / Cyber Monday campaign history, Prime Day-equivalent promotions.
4. **Deal community presence** — posts/deals on mydealz.de, hotukdeals, dealabs.fr,
   cashback listings on Shoop.de, iGraal.fr, TopCashback, Quidco, etc.

Reply ONLY with valid JSON, no surrounding text:
{{
  "marketing_spend_estimate": "~X-YM EUR/year or unknown",
  "google_shopping_presence": "high|medium|low|none",
  "meta_tiktok_activity": "high|medium|low|none",
  "amazon_ads_presence": "high|medium|low|none",
  "affiliate_networks": ["AWIN", "CJ"],
  "coupon_promo_frequency": "frequent|occasional|rare|none",
  "bf_prime_day_signals": true,
  "deal_community_presence": "active|occasional|none",
  "cashback_platform_presence": ["Shoop", "iGraal"],
  "perf_mktg_sophistication": "high|medium|low",
  "affiliate_likelihood": "high|medium|low",
  "promo_intensity_summary": "2 sentences on promo/coupon/deal behaviour",
  "confidence": "high|medium|low"
}}"""

    system = (
        "You are a commercial intelligence analyst with live web search specialised in "
        "eCommerce performance marketing and affiliate programs. "
        "Reply ONLY with the requested JSON object, in English."
    )

    try:
        raw = await _call_perplexity(prompt, system_prompt=system, timeout=60.0, max_tokens=1024)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            logger.info(
                "perplexity.enrich_commercial_intelligence: '%s' → perf_mktg=%s affiliate=%s confidence=%s",
                firma, data.get("perf_mktg_sophistication"), data.get("affiliate_likelihood"),
                data.get("confidence"),
            )
            return data
        logger.warning("perplexity.enrich_commercial_intelligence: kein JSON für '%s'", firma)
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity commercial_intel HTTP %s für '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("perplexity commercial_intel Timeout für '%s'", firma)
    except json.JSONDecodeError as exc:
        logger.warning("perplexity commercial_intel JSON-Fehler für '%s': %s", firma, exc)
    except Exception as exc:
        logger.warning("perplexity commercial_intel Fehler für '%s': %s", firma, exc)

    return {
        "marketing_spend_estimate": "unknown",
        "google_shopping_presence": "none",
        "meta_tiktok_activity": "none",
        "amazon_ads_presence": "none",
        "affiliate_networks": [],
        "coupon_promo_frequency": "none",
        "bf_prime_day_signals": False,
        "deal_community_presence": "none",
        "cashback_platform_presence": [],
        "perf_mktg_sophistication": "low",
        "affiliate_likelihood": "low",
        "promo_intensity_summary": "",
        "confidence": "low",
    }


async def get_news_summary(firma: str) -> str:
    """Recent-news summary in 2-3 English sentences."""
    prompt = f"""Search recent news (2024-2026) about the company "{firma}".

Focus on:
- Funding rounds, growth, expansion
- Partnerships, cooperations
- E-Commerce, affiliate marketing, performance marketing, D2C
- Internationalisation
- Relevant changes in business model

Summarise the most important findings in 2-3 concise English sentences.
If no relevant news is found, reply with: "No recent news found."
Reply with the summary only — no JSON, no title."""

    system = (
        "You are a B2B researcher with live web search. "
        "Reply in concise factual English."
    )

    try:
        summary = await _call_perplexity(prompt, system_prompt=system)
        summary = summary.strip()
        logger.info("perplexity_websearch.get_news_summary: '%s' → %d chars", firma, len(summary))
        return summary or "No recent news found."
    except httpx.HTTPStatusError as exc:
        logger.warning("perplexity news HTTP %s for '%s'", exc.response.status_code, firma)
    except httpx.TimeoutException:
        logger.warning("perplexity news timeout for '%s'", firma)
    except Exception as exc:
        logger.warning("perplexity news error for '%s': %s", firma, exc)

    return "No recent news found."
