"""
curator.py — periodic profile consolidation with dry-run + manual apply.

Ported/adapted from FabBot's curator (Phase 181) onto Serbo_bot's YAML
profile (app/bot/profile.py). The profile only ever grows — the 3-stage
learner appends facts/people/interests but nothing ever merges duplicates
or prunes stale entries. The curator closes that gap WITHOUT auto-deleting:

  1. run_dry_run()  → LLM analyses the profile, builds a concrete proposal
                      (archive duplicates, merge entries, archive stale facts),
                      stores it in curator_state.json with the profile hash it
                      was based on + a 24h expiry, and returns a human report.
  2. user reviews   → /curator apply  (or /curator cancel)
  3. apply_pending() → re-checks the base hash (refuses if the profile changed
                      meanwhile), applies the ops, and ARCHIVES removed items
                      into profile["archived"] rather than deleting them.

Safety rails:
  - Items carrying `_pinned: true` are stripped from the analyzer input AND
    defended again at apply time — the curator never touches them.
  - A pending proposal expires after CURATOR_PROPOSAL_TTL_HOURS.
  - Cooldown (CURATOR_COOLDOWN_DAYS) gates the scheduled dry-run.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.bot import profile
from app.config import (
    OPENROUTER_API_KEY,
    CURATOR_COOLDOWN_DAYS,
    CURATOR_PROPOSAL_TTL_HOURS,
)

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CURATOR_MODEL = "openai/gpt-4o-mini"

_STATE_FILE = Path(__file__).parent.parent / "data" / "curator_state.json"

# Sections the curator is allowed to touch
_DICT_LIST_SECTIONS = ("people", "projects", "goals")
_LIST_SECTIONS = ("interests",)

_ANALYZER_PROMPT = """Du bist ein Gedächtnis-Kurator für ein persönliches Nutzerprofil.
Deine Aufgabe: Dubletten, Redundanzen und veraltete Einträge finden — NICHT neue Fakten erfinden.

Du bekommst das Profil als YAML. Analysiere nur die Abschnitte people, projects, goals, interests, facts.

Finde:
1. DUBLETTEN in people/projects/goals: zwei Einträge, die dieselbe Sache/Person meinen
   (z.B. "Martin" und "Martin Gospodinov"). Gib die Listen-Indizes an und welcher behalten wird.
2. DUBLETTEN in interests: mehrfach genannte/synonyme Interessen (z.B. "Fußball" und "soccer").
3. VERALTETE facts: Schlüssel, die klar abgeschlossen/überholt wirken.

Sei KONSERVATIV: im Zweifel NICHT zusammenführen. Lieber weniger Vorschläge.

Antworte NUR mit validem JSON:
{
  "dict_duplicates": [
    {"section": "people", "indices": [2, 7], "keep_index": 2,
     "merged_entry": {"name": "Martin Gospodinov", "relation": "Kollege"},
     "reason": "kurze Begründung"}
  ],
  "interest_duplicates": [
    {"keep": "Fußball", "remove": ["soccer", "fussball"], "reason": "..."}
  ],
  "stale_facts": [
    {"key": "altes_projekt", "reason": "..."}
  ]
}
Leere Listen sind ok. Keine Erklärungen außerhalb des JSON."""


# ── State ─────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("curator: state save failed: %s", exc)


# ── LLM ───────────────────────────────────────────────────────────────────────


async def _call_llm(system: str, user: str, timeout: float = 30.0) -> str:
    from app.services.llm_client import chat
    from app.config import LLM_CHEAP_MODEL
    return await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=LLM_CHEAP_MODEL, temperature=0.0, max_tokens=900, timeout=timeout,
    )


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# ── Pinned protection ──────────────────────────────────────────────────────────


def _is_pinned(entry: object) -> bool:
    return isinstance(entry, dict) and bool(entry.get("_pinned"))


def _profile_for_analysis(user_id: int) -> dict:
    """Profile copy with pinned dict-items removed, so the analyzer can't even
    propose touching them. interests/facts have no pin concept."""
    p = profile.get_profile(user_id)
    view = {}
    for section in _DICT_LIST_SECTIONS:
        view[section] = [e for e in (p.get(section) or []) if not _is_pinned(e)]
    view["interests"] = list(p.get("interests") or [])
    view["facts"] = dict(p.get("facts") or {})
    return view


# ── Proposal building & sanitising ──────────────────────────────────────────────


def _sanitize(analysis: dict, prof: dict) -> dict:
    """Validate the LLM's analysis against the real profile — drop anything
    out of range / malformed. Returns a clean proposal."""
    out = {"dict_duplicates": [], "interest_duplicates": [], "stale_facts": []}

    for d in analysis.get("dict_duplicates") or []:
        section = d.get("section")
        if section not in _DICT_LIST_SECTIONS:
            continue
        bucket = prof.get(section) or []
        idxs = [i for i in (d.get("indices") or []) if isinstance(i, int) and 0 <= i < len(bucket)]
        if len(idxs) < 2:
            continue
        keep = d.get("keep_index")
        if keep not in idxs:
            keep = idxs[0]
        # Never archive a pinned entry even if the model referenced it
        if any(_is_pinned(bucket[i]) for i in idxs):
            continue
        out["dict_duplicates"].append({
            "section": section,
            "indices": idxs,
            "keep_index": keep,
            "merged_entry": d.get("merged_entry") if isinstance(d.get("merged_entry"), dict) else None,
            "reason": str(d.get("reason", ""))[:160],
        })

    interests = {str(i).lower() for i in (prof.get("interests") or [])}
    for d in analysis.get("interest_duplicates") or []:
        keep = d.get("keep")
        remove = [r for r in (d.get("remove") or []) if str(r).lower() in interests and str(r).lower() != str(keep).lower()]
        if keep and remove:
            out["interest_duplicates"].append({
                "keep": keep, "remove": remove, "reason": str(d.get("reason", ""))[:160],
            })

    facts = prof.get("facts") or {}
    for d in analysis.get("stale_facts") or []:
        key = d.get("key")
        if key in facts:
            out["stale_facts"].append({"key": key, "reason": str(d.get("reason", ""))[:160]})

    return out


def _proposal_is_empty(p: dict) -> bool:
    return not (p.get("dict_duplicates") or p.get("interest_duplicates") or p.get("stale_facts"))


# ── Report ──────────────────────────────────────────────────────────────────────


def format_report(proposal: dict, prof: dict, expires_at: str) -> str:
    lines = ["🧹 *Curator — Vorschlag zur Profil-Bereinigung*\n"]

    for d in proposal.get("dict_duplicates", []):
        bucket = prof.get(d["section"]) or []
        names = []
        for i in d["indices"]:
            e = bucket[i]
            names.append(e.get("name") or e.get("text") or str(e))
        keep_e = bucket[d["keep_index"]]
        keep_name = keep_e.get("name") or keep_e.get("text") or str(keep_e)
        lines.append(
            f"• *{d['section']}*: {' + '.join(names)} → behalte {keep_name!r}, archiviere Rest"
        )
        if d.get("reason"):
            lines.append(f"  _{d['reason']}_")

    for d in proposal.get("interest_duplicates", []):
        lines.append(
            f"• *interests*: behalte {d['keep']!r}, entferne {', '.join(d['remove'])}"
        )
        if d.get("reason"):
            lines.append(f"  _{d['reason']}_")

    for d in proposal.get("stale_facts", []):
        lines.append(f"• *facts*: archiviere {d['key']!r}")
        if d.get("reason"):
            lines.append(f"  _{d['reason']}_")

    lines.append(f"\n_Bestätige mit_ `/curator apply` _oder verwirf mit_ `/curator cancel`.")
    lines.append(f"_Vorschlag verfällt: {expires_at[:16].replace('T', ' ')} UTC._")
    return "\n".join(lines)


# ── Public API ──────────────────────────────────────────────────────────────────


async def run_dry_run(user_id: int, *, force: bool = False) -> str | None:
    """Analyse the profile and store a pending proposal. Returns a report
    string, or None if there's nothing to consolidate."""
    state = _load_state()

    if not force:
        last = (state.get("last_run") or {}).get(str(user_id))
        if last:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if _now() - last_dt < timedelta(days=CURATOR_COOLDOWN_DAYS):
                    return None
            except ValueError:
                pass

    view = _profile_for_analysis(user_id)
    if not any(view.get(s) for s in (*_DICT_LIST_SECTIONS, "interests", "facts")):
        return None

    profile_yaml = json.dumps(view, ensure_ascii=False, indent=1)[:8000]
    try:
        raw = await _call_llm(_ANALYZER_PROMPT, f"PROFIL:\n{profile_yaml}")
    except Exception as exc:
        logger.warning("curator: analyzer LLM failed: %s", exc)
        return None

    analysis = _extract_json(raw) or {}
    full = profile.get_profile(user_id)
    proposal = _sanitize(analysis, full)

    # Always record the run timestamp so the cooldown advances
    state.setdefault("last_run", {})[str(user_id)] = _now_iso()

    if _proposal_is_empty(proposal):
        state.setdefault("pending", {}).pop(str(user_id), None)
        _save_state(state)
        return None

    expires_at = (_now() + timedelta(hours=CURATOR_PROPOSAL_TTL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.setdefault("pending", {})[str(user_id)] = {
        "proposal": proposal,
        "base_hash": profile.profile_hash(user_id),
        "created_at": _now_iso(),
        "expires_at": expires_at,
    }
    _save_state(state)
    return format_report(proposal, full, expires_at)


def _pending_for(user_id: int) -> dict | None:
    pending = (_load_state().get("pending") or {}).get(str(user_id))
    if not pending:
        return None
    try:
        exp = datetime.strptime(pending["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if _now() > exp:
            return None
    except (KeyError, ValueError):
        return None
    return pending


async def apply_pending(user_id: int) -> tuple[bool, str]:
    """Apply the stored proposal. Returns (ok, message)."""
    pending = _pending_for(user_id)
    if not pending:
        return False, "Kein gültiger Vorschlag (oder verfallen). Erst `/curator run`."

    if profile.profile_hash(user_id) != pending["base_hash"]:
        _invalidate(user_id)
        return False, "Profil hat sich seit dem Vorschlag geändert — verworfen. `/curator run` neu starten."

    proposal = pending["proposal"]
    new_profile = profile.get_profile(user_id)
    archived = new_profile.setdefault("archived", [])
    n_archived = 0
    n_merged = 0

    # 1) dict-list duplicates — archive non-keep indices, optionally merge
    for d in proposal.get("dict_duplicates", []):
        section = d["section"]
        bucket = new_profile.get(section) or []
        keep_i = d["keep_index"]
        if not (0 <= keep_i < len(bucket)) or _is_pinned(bucket[keep_i]):
            continue
        drop_is = sorted((i for i in d["indices"] if i != keep_i), reverse=True)
        for i in drop_is:
            if not (0 <= i < len(bucket)) or _is_pinned(bucket[i]):
                continue
            archived.append({
                "section": section, "entry": bucket[i],
                "reason": d.get("reason", ""), "archived_at": _now_iso(),
            })
            bucket.pop(i)
            n_archived += 1
        # re-find keep entry after pops (its index shifted) by identity merge
        if d.get("merged_entry"):
            # keep entry is whichever survived; merge into the first matching name
            merged = d["merged_entry"]
            mname = (merged.get("name") or merged.get("text") or "").strip().lower()
            for e in bucket:
                ename = (e.get("name") or e.get("text") or "").strip().lower()
                if ename and (ename == mname or mname.startswith(ename) or ename.startswith(mname)):
                    e.update({k: v for k, v in merged.items() if v})
                    n_merged += 1
                    break

    # 2) interest duplicates
    for d in proposal.get("interest_duplicates", []):
        interests = new_profile.get("interests") or []
        remove_low = {str(r).lower() for r in d["remove"]}
        kept = []
        for it in interests:
            if str(it).lower() in remove_low:
                archived.append({"section": "interests", "entry": it,
                                 "reason": d.get("reason", ""), "archived_at": _now_iso()})
                n_archived += 1
            else:
                kept.append(it)
        new_profile["interests"] = kept

    # 3) stale facts
    facts = new_profile.get("facts") or {}
    for d in proposal.get("stale_facts", []):
        key = d["key"]
        if key in facts:
            archived.append({"section": "facts", "entry": {key: facts[key]},
                             "reason": d.get("reason", ""), "archived_at": _now_iso()})
            facts.pop(key, None)
            n_archived += 1

    ok = await profile.write_profile(user_id, new_profile, expected_hash=pending["base_hash"])
    if not ok:
        _invalidate(user_id)
        return False, "Profil hat sich gerade geändert — Schreiben abgebrochen. `/curator run` neu."

    _invalidate(user_id)
    return True, f"✅ Bereinigt: {n_archived} archiviert, {n_merged} zusammengeführt."


def _invalidate(user_id: int) -> None:
    state = _load_state()
    (state.get("pending") or {}).pop(str(user_id), None)
    _save_state(state)


def cancel_pending(user_id: int) -> str:
    if _pending_for(user_id):
        _invalidate(user_id)
        return "❌ Curator-Vorschlag verworfen."
    return "Kein offener Vorschlag."


def get_status(user_id: int) -> str:
    pending = _pending_for(user_id)
    if pending:
        p = pending["proposal"]
        n = (len(p.get("dict_duplicates", [])) + len(p.get("interest_duplicates", []))
             + len(p.get("stale_facts", [])))
        return (f"🧹 Offener Vorschlag mit {n} Änderung(en), verfällt "
                f"{pending['expires_at'][:16].replace('T', ' ')} UTC.\n"
                f"`/curator apply` · `/curator cancel`")
    last = (_load_state().get("last_run") or {}).get(str(user_id))
    last_str = f" Letzter Lauf: {last[:10]}." if last else ""
    return f"🧹 Kein offener Vorschlag.{last_str}\n`/curator run` startet eine Analyse."
