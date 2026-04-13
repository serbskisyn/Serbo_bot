import logging
import re
import tempfile
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Datenvisualisierungs-Experte. NUR ausführbaren Python-Code ausgeben. "
    "Kein Text vor/nach Code-Block. matplotlib verwenden. "
    "Chart speichern: plt.savefig(OUTPUT_PATH, bbox_inches='tight', dpi=150). "
    "OUTPUT_PATH bereits definiert. Kein plt.show(). Kein import für OUTPUT_PATH."
)


def _extract_code(raw: str) -> str:
    match = re.search(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw.strip()


async def generate_chart(text: str) -> bytes | None:
    """Generiert einen Chart und gibt PNG-Bytes zurück."""
    raw = await ask_llm(text, history=[], system_prompt=SYSTEM_PROMPT)
    code = _extract_code(raw)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "chart.png")
        try:
            exec_globals = {"OUTPUT_PATH": output_path}
            exec(compile(code, "<chart>", "exec"), exec_globals)

            if not os.path.exists(output_path):
                logger.error("Chart wurde nicht gespeichert.")
                return None

            with open(output_path, "rb") as f:
                return f.read()

        except Exception as e:
            logger.error(f"Chart-Ausführung fehlgeschlagen: {e}\nCode:\n{code}")
            return None
