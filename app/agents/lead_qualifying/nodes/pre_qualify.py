"""
pre_qualify.py — LangGraph node: fast pre-qualification on raw lead data only.

Uses a cheap, fast LLM call (GPT-4o-mini) on the raw Sheet fields to determine
whether a lead is worth enriching at all. No web search involved.

Labels:
  HIGH  — clear potential for ≥1 Atolls platform → full enrichment pipeline
  LOW   — ambiguous signal → enrich anyway, lower priority
  SKIP  — obvious non-fit → write as FILTERED to sheet, no enrichment, no Telegram
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.lead_qualifying.prompts import PRE_QUALIFY_SYSTEM, PRE_QUALIFY_USER
from app.agents.lead_qualifying.state import LeadState
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Fast, cheap model for the pre-qualify step — no need for a heavy model here
_PRE_QUALIFY_MODEL = "openai/gpt-4o-mini"

# ── Deterministische Fake-Lead-Detektoren ────────────────────────────────────
# Ziel: Bots/Spam/Test-Einträge ohne LLM-Call rauskicken (spart Geld + Latenz)

# ── Agency-Erkennung ─────────────────────────────────────────────────────────
# Bekannte Media-/Werbeagenturen und deren E-Mail-Domains — diese Leads kommen
# im Auftrag eines Advertisers, der Advertiser selbst ist aber unbekannt.

_AGENCY_COMPANY_TOKENS = (
    "publicis", "dentsu", "wpp ", "omnicom", "interpublic", "havas",
    "ogilvy", "bbdo", "mccann", "grey ", "saatchi", "tbwa", "jwt ",
    "leo burnett", "mindshare", "mediacom", "zenith media", "zenithoptimedia",
    "starcom", "wavemaker", "carat ", "mediaedge", "cheil",
    "razorfish", "digitas", "vccp", "iris worldwide",
    "media agency", "advertising agency", "media group",
    "publicisgroupe", "publicis groupe", "publicis media",
    "dentsuaegis", "dentsu aegis",
)

_AGENCY_EMAIL_DOMAINS = (
    "@publicisgroupe.com", "@publicismedia.com",
    "@dentsuaegis.com", "@dentsu.com",
    "@wpp.com", "@ogilvy.com", "@bbdo.com", "@mccann.com",
    "@interpublic.com", "@havas.com", "@omnicom.com",
    "@mindshareworld.com", "@mediacom.com", "@carat.com",
    "@zenithmedia.com", "@wavemaker.com", "@starcom.com",
    "@tbwa.com", "@saatchi.com",
)


def _deterministic_agency(firma: str, email: str) -> str | None:
    """Returns reason string if company is clearly a media/advertising agency, else None."""
    firma_low = firma.strip().lower()
    email_low = email.strip().lower()
    for token in _AGENCY_COMPANY_TOKENS:
        if token in firma_low:
            return f"'{firma}' is a media/advertising agency — actual advertiser not identified"
    for domain in _AGENCY_EMAIL_DOMAINS:
        if email_low.endswith(domain):
            return f"Email domain '{domain}' belongs to a known media agency network"
    return None


_SPAM_EMAIL_PATTERNS = (
    "test@", "asdf", "foo@", "bar@", "noreply", "no-reply",
    "example.com", "tempmail", "mailinator", "yopmail", "guerrillamail",
    "10minutemail", "dispostable", "fakeinbox",
)

# Häufige Fake-/Test-Vornamen (lowercase, exakter Match)
_SPAM_NAME_TOKENS = {
    "test", "tester", "testing", "user", "admin", "asdf", "qwer", "xxxx",
    "abc", "abcd", "demo", "sample", "dummy", "fake", "anonymous", "null",
    "none", "n/a", "na", "nobody", "x", "y", "z",
}

_MIN_FIRMA_LEN = 3
_MIN_NAME_LEN  = 2

# Erkennt "zufällige" Strings: viele Ziffern, kaum Vokale, Tastatur-Rolls.
_VOWELS = set("aeiouäöüy")
_KEYBOARD_ROLLS = ("qwer", "asdf", "zxcv", "yxcv", "1234", "abcd", "0000", "1111")


def _looks_random(s: str) -> bool:
    """True wenn String 'getippter Müll' wirkt: keine Vokale, oder Tastatur-Rolls."""
    import re as _re
    s = s.strip().lower()
    if len(s) < 3:
        return False
    digits  = sum(c.isdigit() for c in s)
    letters = sum(c.isalpha() for c in s)
    # Mehrheitlich Ziffern und kurz → "User1234"-ähnlich
    if letters > 0 and digits >= letters and len(s) <= 12:
        return True
    # Kein einziger Vokal in Buchstaben-Teil — nur bei reinen Buchstaben-Strings
    # (sonst False-Positives bei kurzen Akronymen mit Konsonanten + Zahlen)
    if letters >= 4 and digits == 0 and not any(c in _VOWELS for c in s if c.isalpha()):
        return True
    # Tastatur-Roll-Pattern enthalten
    if any(r in s for r in _KEYBOARD_ROLLS):
        return True
    # Direkte Wiederholungen: "aaaa", "xxxx", "1111" — 4+ gleiche Zeichen hintereinander
    if _re.search(r"(.)\1{3,}", s):
        return True
    return False


def _name_looks_fake(name: str) -> str | None:
    """Returns English reason string if name looks fake, else None."""
    if not name:
        return None
    low = name.strip().lower()
    if low in _SPAM_NAME_TOKENS:
        return f"Name '{name}' is a typical fake/test token"
    tokens = low.split()
    if len(tokens) >= 2 and len(set(tokens)) == 1:
        return f"Name '{name}' repeats itself (test entry?)"
    if _looks_random(low.replace(" ", "")):
        return f"Name '{name}' looks randomly typed (no vowels / keyboard roll / repeats)"
    return None


def _firma_looks_fake(firma: str) -> str | None:
    """Returns English reason string if company looks fake, else None."""
    if not firma:
        return None
    low = firma.strip().lower()
    if low in _SPAM_NAME_TOKENS:
        return f"Company '{firma}' is a typical fake/test token"
    if _looks_random(low):
        return f"Company '{firma}' looks randomly typed"
    return None


def _deterministic_skip(vorname: str, nachname: str, firma: str, email: str) -> str | None:
    """Schnelle Heuristik vor dem LLM. Returns reason-string wenn SKIP, sonst None.

    Geprüft (in Reihenfolge):
      1. Mindest-Längen für Firma + Name
      2. Spam-E-Mail-Muster (test@, mailinator, …)
      3. Identische Müll-Daten (Firma == Vorname)
      4. Fake-Name-Patterns (Test, User, Wiederholungen, Zufalls-Strings)
      5. Fake-Firma-Patterns (Zufalls-Strings, Spam-Token)
    """
    if len(firma) < _MIN_FIRMA_LEN and not nachname:
        return f"Company and last name too short ('{firma}', '{nachname}') — no usable data"
    if len(firma) < _MIN_FIRMA_LEN and len(vorname) < _MIN_NAME_LEN:
        return f"Company '{firma}' and first name '{vorname}' too short — invalid lead"
    if not firma and not email:
        return "Neither company nor email present"

    email_low = email.lower()
    for pat in _SPAM_EMAIL_PATTERNS:
        if pat in email_low:
            return f"Email contains spam/test pattern '{pat}'"

    if vorname and firma and vorname.lower() == firma.lower() and len(firma) <= 3:
        return f"Identical junk data ('{firma}'={vorname}) — not a real lead"

    full_name = f"{vorname} {nachname}".strip()
    if (reason := _name_looks_fake(full_name)):
        return reason
    if (reason := _name_looks_fake(vorname)):
        return reason
    if (reason := _firma_looks_fake(firma)):
        return reason

    if firma and vorname and firma.lower() == vorname.lower() and len(firma) <= 6:
        return f"Company '{firma}' identical to first name — likely auto-fill junk"

    return None


async def pre_qualify_node(state: LeadState) -> LeadState:
    """
    Quick assessment of the raw lead fields.

    1. Deterministische SKIP-Heuristiken (kein LLM-Call bei offensichtlichem Müll)
    2. LLM-Klassifikation für alles andere

    Reads:  state["current_lead"]
    Writes: state["pre_qualify_label"] ("HIGH" | "LOW" | "SKIP")
            state["pre_qualify_reason"] (1-sentence explanation)
    """
    lead = state.get("current_lead", {})
    vorname = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma = str(lead.get("Firma", "")).strip()
    email = str(lead.get("E-Mail", "")).strip()
    quelle = str(lead.get("Quelle", "")).strip()

    logger.info("pre_qualify: '%s %s' @ '%s'", vorname, nachname, firma)

    # 1a. Agentur-Erkennung (vor Spam-Check, da Agenturen gültige Daten haben)
    agency_reason = _deterministic_agency(firma, email)
    if agency_reason:
        logger.info("pre_qualify: '%s' → AGENCY (deterministisch) | %s", firma, agency_reason)
        return {
            **state,
            "pre_qualify_label": "AGENCY",
            "pre_qualify_reason": agency_reason,
        }

    # 1b. Schneller deterministischer SKIP-Check
    auto_skip = _deterministic_skip(vorname, nachname, firma, email)
    if auto_skip:
        logger.info("pre_qualify: '%s' → SKIP (deterministisch) | %s", firma, auto_skip)
        return {
            **state,
            "pre_qualify_label": "SKIP",
            "pre_qualify_reason": auto_skip,
        }

    label = "LOW"       # safe default: enrich anyway
    reason = ""

    try:
        prompt = PRE_QUALIFY_USER.format(
            vorname=vorname,
            nachname=nachname,
            firma=firma,
            email=email,
            quelle=quelle,
        )
        raw = await ask_llm(
            user_text=prompt,
            system_prompt=PRE_QUALIFY_SYSTEM,
        )
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            raw_label = str(data.get("label", "LOW")).upper().strip()
            label = raw_label if raw_label in ("HIGH", "LOW", "SKIP", "AGENCY") else "LOW"
            reason = str(data.get("reason", "")).strip()
        else:
            logger.warning("pre_qualify: kein JSON in Antwort für '%s'", firma)

    except Exception as exc:
        logger.warning("pre_qualify: Fehler für '%s': %s", firma, exc)

    logger.info("pre_qualify: '%s' → %s | %s", firma, label, reason)

    return {
        **state,
        "pre_qualify_label": label,
        "pre_qualify_reason": reason,
    }


def route_after_pre_qualify(state: LeadState) -> str:
    """
    LangGraph conditional edge: decide which node to run after pre_qualify.

    SKIP / AGENCY / LOW → collect_filtered_result (write to sheet, no enrichment)
    HIGH → enrich_contact (full pipeline)

    LOW is routed out here too: a weak lead isn't worth the enrichment chain
    (4 Perplexity calls + Pepper subprocess). It's recorded with its
    pre_qualify_label/reason, classified FILTERED, and kept out of the push.
    """
    label = state.get("pre_qualify_label", "LOW")
    if label in ("SKIP", "AGENCY", "LOW"):
        logger.info("pre_qualify router: %s → collect_filtered_result", label)
        return "collect_filtered_result"
    logger.info("pre_qualify router: %s → enrich_contact", label)
    return "enrich_contact"
