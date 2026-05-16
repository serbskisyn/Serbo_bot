import json
import logging
import os
import re
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist Datenvisualisierungs-Experte. Antworte AUSSCHLIESSLICH mit gültigem JSON "
    "nach diesem Schema (kein Text vor/nach dem JSON, keine Markdown-Codefences):\n"
    "{\n"
    '  "type": "line" | "bar" | "scatter",\n'
    '  "title": "string",\n'
    '  "xlabel": "string (optional)",\n'
    '  "ylabel": "string (optional)",\n'
    '  "series": [\n'
    '    {"label": "string", "x": [Zahlen oder Strings], "y": [Zahlen]}\n'
    "  ]\n"
    "}\n"
    "Verwende plausible Beispieldaten aus deinem Wissen, wenn keine Daten gegeben sind. "
    "Wenn keine sinnvolle Visualisierung möglich ist, gib leeres series-Array zurück."
)

ALLOWED_TYPES = {"line", "bar", "scatter"}


def _parse_spec(raw: str) -> dict | None:
    """Extract and parse JSON spec from LLM response. Tolerant zu Markdown-Fences."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = (match.group(1) if match else raw).strip()
    try:
        spec = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            start = candidate.index("{")
            end = candidate.rindex("}") + 1
            spec = json.loads(candidate[start:end])
        except (ValueError, json.JSONDecodeError):
            return None
    return spec if isinstance(spec, dict) else None


def _render(spec: dict, output_path: str) -> bool:
    """Render JSON spec to PNG. Returns True on success."""
    chart_type = spec.get("type", "line")
    if chart_type not in ALLOWED_TYPES:
        logger.error("Chart-Spec: unbekannter Typ %r", chart_type)
        return False
    series = spec.get("series") or []
    if not series:
        logger.error("Chart-Spec: leere series")
        return False

    fig, ax = plt.subplots(figsize=(10, 6))
    try:
        for s in series:
            x = s.get("x") or []
            y = s.get("y") or []
            label = str(s.get("label", "")) or None
            if chart_type == "line":
                ax.plot(x, y, label=label, linewidth=2, marker="o")
            elif chart_type == "bar":
                ax.bar(x, y, label=label)
            elif chart_type == "scatter":
                ax.scatter(x, y, label=label)
        if spec.get("title"):
            ax.set_title(str(spec["title"]), fontsize=14)
        if spec.get("xlabel"):
            ax.set_xlabel(str(spec["xlabel"]))
        if spec.get("ylabel"):
            ax.set_ylabel(str(spec["ylabel"]))
        if any(s.get("label") for s in series):
            ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_path, bbox_inches="tight", dpi=150)
        return True
    except Exception as e:
        logger.error("Chart-Render fehlgeschlagen: %s", e)
        return False
    finally:
        plt.close(fig)


async def generate_chart(text: str) -> bytes | None:
    """Generiert einen Chart und gibt PNG-Bytes zurück."""
    raw = await ask_llm(text, history=[], system_prompt=SYSTEM_PROMPT)
    spec = _parse_spec(raw)
    if spec is None:
        logger.error("Chart-Spec konnte nicht geparsed werden: %s", raw[:300])
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "chart.png")
        if not _render(spec, output_path):
            return None
        with open(output_path, "rb") as f:
            return f.read()
