"""
granola_lookup.py — Pull meeting transcripts/commitments via Granola MCP.

Mirrors the pepper_lookup pattern: a single Claude subprocess hosts the
Atolls Granola MCP connector. We ask it for the user's recent meetings
in the lookback window, then extract:
  • commitments  — concrete action items the user owns
  • decisions    — explicit decisions taken (logged as "context" todos)
  • mentioned_people — names that surfaced (Phase 4 will feed these
    into the relationship-alert system)

Returns a structured dict per meeting:
  {
    "title": str,
    "date":  ISO date,
    "commitments":     [str, ...],
    "decisions":       [str, ...],
    "mentioned_people":[str, ...],
  }

Never raises — pipeline keeps moving on MCP errors.
"""
from __future__ import annotations

import json
import logging
import re

from app.services.mcp_runner import run_mcp_subprocess

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 240
_MAX_ATTEMPTS = 2


_PROMPT_TEMPLATE = """You are extracting meeting commitments for a personal-productivity bot.

STEP 1 — MUST: call `mcp__claude_ai_Granola__list_meetings` to enumerate ALL meetings from the last {lookback_hours} hours. Do NOT skip this call. Do NOT answer from memory.

STEP 2 — For each meeting returned, call `mcp__claude_ai_Granola__get_meeting_transcript` to fetch its notes/transcript.

STEP 3 — For each meeting build an entry with:
  • title              — verbatim
  • date               — ISO YYYY-MM-DD
  • commitments        — concrete action items the user committed to (verbs: send, prepare, share, follow up, schedule, draft, review). Max 5, each ≤ 80 chars, imperative voice.
  • decisions          — explicit decisions made in the meeting. Max 5.
  • mentioned_people   — distinct names of people other than the user. Max 5.

STEP 4 — Reply with ONLY this JSON object (no prose, no markdown fences, no commentary):

{{
  "meetings": [
    {{
      "title":  "...",
      "date":   "YYYY-MM-DD",
      "commitments":      ["..."],
      "decisions":        ["..."],
      "mentioned_people": ["..."]
    }}
  ]
}}

If `list_meetings` returns zero meetings for the window, reply with the literal: {{"meetings": []}}
If the MCP tool errors or is unavailable, reply with: {{"meetings": [], "error": "<short reason>"}}
A Python script will json.loads() your reply — surrounding text breaks it."""


def _extract_json(raw: str) -> dict | None:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start: end + 1])
        except json.JSONDecodeError:
            return None
    return None


_EMPTY_RESULT: dict = {"meetings": [], "error": None}


async def _one_attempt(lookback_hours: int, attempt: int) -> tuple[dict | None, str]:
    """Single subprocess call. Returns (parsed_payload or None, error_marker)."""
    prompt = _PROMPT_TEMPLATE.format(lookback_hours=int(lookback_hours))
    try:
        raw = await run_mcp_subprocess(prompt, timeout=_TIMEOUT_SEC, label=f"granola#{attempt}")
    except Exception as exc:
        return None, f"subprocess: {exc}"

    if not raw or raw.startswith("❌") or raw.startswith("⏳"):
        return None, f"runner-error: {(raw or 'no output')[:200]}"

    parsed = _extract_json(raw)
    if parsed is None:
        return None, f"json-parse-failed: {raw[:200]!r}"

    return parsed, ""


async def get_recent_meetings(lookback_hours: int = 30) -> dict:
    """Query Granola via the MCP subprocess. Always returns a dict.

    Retries up to _MAX_ATTEMPTS times — Pi-Claude cold-starts sometimes
    skip the MCP tool entirely and reply with an empty list. A second
    attempt usually succeeds because the subprocess pool is warm.

    Schema: {"meetings": [{"title", "date", "commitments", "decisions", "mentioned_people"}], "error": str|None}.
    """
    logger.info("granola_lookup: starting (lookback=%dh)", lookback_hours)

    parsed: dict | None = None
    last_error = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        parsed, err = await _one_attempt(lookback_hours, attempt)
        if parsed is None:
            last_error = err
            logger.warning("granola_lookup: attempt %d failed: %s", attempt, err)
            continue

        # If we got a parseable payload with meetings — success
        meetings_in = parsed.get("meetings") or []
        if meetings_in:
            last_error = ""
            break

        # Empty list with an explicit error → treat as failure for retry
        if parsed.get("error"):
            last_error = f"mcp-error: {parsed['error']}"
            logger.warning("granola_lookup: attempt %d MCP-error: %s", attempt, parsed["error"])
            continue

        # Empty meetings without error — could be Cold-Start swallowing the
        # tool call OR genuinely no meetings. Retry once to disambiguate.
        if attempt < _MAX_ATTEMPTS:
            logger.info("granola_lookup: attempt %d empty — retrying", attempt)
            continue
        last_error = ""  # final attempt also empty → genuinely no meetings
        break

    if parsed is None:
        return {**_EMPTY_RESULT, "error": last_error or "unknown failure"}

    raw_meetings = parsed.get("meetings") or []
    meetings = []
    for m in raw_meetings:
        if not isinstance(m, dict):
            continue
        meetings.append({
            "title":            str(m.get("title") or "").strip(),
            "date":             str(m.get("date") or "").strip(),
            "commitments":      [str(c).strip() for c in (m.get("commitments") or []) if str(c).strip()][:5],
            "decisions":        [str(d).strip() for d in (m.get("decisions") or []) if str(d).strip()][:5],
            "mentioned_people": [str(p).strip() for p in (m.get("mentioned_people") or []) if str(p).strip()][:5],
        })

    logger.info(
        "granola_lookup: %d meetings, %d total commitments",
        len(meetings), sum(len(m["commitments"]) for m in meetings),
    )
    return {"meetings": meetings, "error": last_error or None}
