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

_TIMEOUT_SEC = 180


_PROMPT_TEMPLATE = """Hi! I'm running an inbox-aware briefing bot and need a structured digest of recent meetings from Granola.

Could you please call mcp__claude_ai_Granola__list_meetings (or query_granola_meetings) to fetch the meetings from the last {lookback_hours} hours, then for each meeting fetch the transcript via mcp__claude_ai_Granola__get_meeting_transcript.

For each meeting, extract:
  • commitments      — concrete action items the user appeared to commit to (verbs: send, prepare, share, follow up, schedule, draft, review, decide…)
  • decisions        — explicit decisions reached during the meeting
  • mentioned_people — distinct names of people mentioned (not the user)

Reply with a single JSON object — a Python script will json.loads() the answer, so no surrounding prose / no markdown fences:

{{
  "meetings": [
    {{
      "title":  "<meeting title>",
      "date":   "<ISO date YYYY-MM-DD>",
      "commitments":      ["<short imperative phrase>", ...],
      "decisions":        ["<short phrase>", ...],
      "mentioned_people": ["<name>", ...]
    }}
  ]
}}

If the Granola tool returns no meetings, reply: {{"meetings": []}}
If a meeting has no commitments, use an empty list.
Limit each list to 5 items max per meeting. Keep each phrase under 80 chars."""


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


async def get_recent_meetings(lookback_hours: int = 30) -> dict:
    """Query Granola via the MCP subprocess. Always returns a dict.

    Schema: {"meetings": [{"title", "date", "commitments", "decisions", "mentioned_people"}], "error": str|None}.
    """
    prompt = _PROMPT_TEMPLATE.format(lookback_hours=int(lookback_hours))
    logger.info("granola_lookup: starting (lookback=%dh)", lookback_hours)

    try:
        raw = await run_mcp_subprocess(prompt, timeout=_TIMEOUT_SEC, label="granola")
    except Exception as exc:
        logger.warning("granola_lookup: subprocess-Exception: %s", exc)
        return {**_EMPTY_RESULT, "error": f"subprocess: {exc}"}

    if not raw or raw.startswith("❌") or raw.startswith("⏳"):
        logger.warning("granola_lookup: subprocess returned error marker: %s", (raw or "")[:200])
        return {**_EMPTY_RESULT, "error": (raw or "no output")[:300]}

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("granola_lookup: JSON parse failed; raw=%r", raw[:300])
        return {**_EMPTY_RESULT, "error": "JSON parse failed"}

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
    return {"meetings": meetings, "error": None}
