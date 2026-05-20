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
    """Returns reason-string falls Name verdächtig ist, sonst None."""
    if not name:
        return None
    low = name.strip().lower()
    if low in _SPAM_NAME_TOKENS:
        return f"Name '{name}' ist ein typisches Fake-/Test-Token"
    # "Test Test", "User User" → Wiederholungen
    tokens = low.split()
    if len(tokens) >= 2 and len(set(tokens)) == 1:
        return f"Name '{name}' wiederholt sich identisch (Test-Eintrag?)"
    if _looks_random(low.replace(" ", "")):
        return f"Name '{name}' wirkt zufällig getippt (kein Vokal / Tastatur-Roll / Wiederholungen)"
    return None


def _firma_looks_fake(firma: str) -> str | None:
    """Returns reason-string falls Firma verdächtig ist, sonst None."""
    if not firma:
        return None
    low = firma.strip().lower()
    if low in _SPAM_NAME_TOKENS:
        return f"Firma '{firma}' ist ein typisches Fake-/Test-Token"
    if _looks_random(low):
        return f"Firma '{firma}' wirkt zufällig getippt"
    # Firma identisch zu Vor- oder Nachname → meist Auto-Fill-Müll
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
        return f"Firmenname und Nachname zu kurz ('{firma}', '{nachname}') — keine verwertbaren Daten"
    if len(firma) < _MIN_FIRMA_LEN and len(vorname) < _MIN_NAME_LEN:
        return f"Firma '{firma}' und Vorname '{vorname}' zu kurz — ungültiger Lead"
    if not firma and not email:
        return "Weder Firma noch E-Mail vorhanden"

    email_low = email.lower()
    for pat in _SPAM_EMAIL_PATTERNS:
        if pat in email_low:
            return f"E-Mail enthält Spam-/Test-Muster '{pat}'"

    if vorname and firma and vorname.lower() == firma.lower() and len(firma) <= 3:
        return f"Identische Müll-Daten ('{firma}'={vorname}) — kein echter Lead"

    full_name = f"{vorname} {nachname}".strip()
    if (reason := _name_looks_fake(full_name)):
        return reason
    if (reason := _name_looks_fake(vorname)):
        return reason
    if (reason := _firma_looks_fake(firma)):
        return reason

    # Firma exakt gleich Vor- oder Nachname (Auto-Fill-Müll bei kurzen Strings)
    if firma and vorname and firma.lower() == vorname.lower() and len(firma) <= 6:
        return f"Firma '{firma}' identisch zu Vorname — wahrscheinlich Auto-Fill-Müll"

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

    # 1. Schneller deterministischer SKIP-Check
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
            label = raw_label if raw_label in ("HIGH", "LOW", "SKIP") else "LOW"
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

    SKIP  → collect_filtered_result (write to sheet as FILTERED, no enrichment)
    HIGH / LOW → enrich_contact (full pipeline)
    """
    label = state.get("pre_qualify_label", "LOW")
    if label == "SKIP":
        logger.info("pre_qualify router: SKIP → collect_filtered_result")
        return "collect_filtered_result"
    logger.info("pre_qualify router: %s → enrich_contact", label)
    return "enrich_contact"
