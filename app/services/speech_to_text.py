import logging
import os
import tempfile
import whisper
from pydub import AudioSegment

logger = logging.getLogger(__name__)

_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        logger.info("Lade Whisper-Modell (base)...")
        _whisper_model = whisper.load_model("base")
        logger.info("Whisper-Modell geladen.")
    return _whisper_model

async def transcribe_voice(ogg_bytes: bytes) -> str:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, "voice.ogg")
            wav_path = os.path.join(tmpdir, "voice.wav")

            with open(ogg_path, "wb") as f:
                f.write(ogg_bytes)

            audio = AudioSegment.from_ogg(ogg_path)
            audio.export(wav_path, format="wav")

            model = get_whisper_model()
            result = model.transcribe(wav_path, language="de")
            transcript = result["text"].strip()

            logger.info(f"Transkription: '{transcript[:80]}'")
            return transcript

    except Exception as e:
        logger.error(f"Fehler bei Transkription: {e}", exc_info=True)
        return None
