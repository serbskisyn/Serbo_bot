import io
import logging
import edge_tts

logger = logging.getLogger(__name__)


async def synthesize(text: str, voice: str = "de-DE-KatjaNeural") -> bytes | None:
    """Synthesisiert Text via edge-tts und gibt MP3-Bytes zurück."""
    if not text or not text.strip():
        return None
    # Markdown-Artefakte entfernen die TTS-Ausgabe stören
    clean = (
        text.replace("*", "").replace("_", "").replace("`", "")
            .replace("#", "").replace(">", "").strip()
    )
    if not clean:
        return None
    try:
        communicate = edge_tts.Communicate(clean[:3000], voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        data = buf.getvalue()
        if not data:
            return None
        logger.info("TTS: %d Zeichen → %d Bytes MP3", len(clean), len(data))
        return data
    except Exception as e:
        logger.warning("TTS Fehler: %s", e)
        return None
