"""edge-tts TTS: text_to_speech(text, voice) -> bytes (OGG/OPUS)"""

from __future__ import annotations

import io
import logging
from typing import Optional

import edge_tts
from pydub import AudioSegment

logger = logging.getLogger(__name__)

# Default Russian voice
DEFAULT_VOICE = "ru-RU-DmitryNeural"


async def text_to_speech(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Generate speech from text using edge-tts.
    
    Returns audio as OGG/OPUS bytes suitable for Telegram voice messages.
    """
    try:
        communicate = edge_tts.Communicate(text, voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        
        if not audio_data:
            logger.warning("text_to_speech received no audio data for: %s", text[:50])
            return b""
        
        # edge-tts returns MP3 by default; convert to OGG/OPUS for Telegram voice
        audio = AudioSegment.from_mp3(io.BytesIO(audio_data))
        ogg_buf = io.BytesIO()
        audio.export(ogg_buf, format="ogg", codec="libopus")
        ogg_buf.seek(0)
        return ogg_buf.read()
        
    except Exception as e:
        logger.error(f"text_to_speech error: {e}")
        raise


async def list_voices(lang: Optional[str] = None) -> list[dict]:
    """List available edge-tts voices, optionally filtered by language code
    (e.g. 'ru' for Russian voices). Returns list of {name, gender}."""
    all_voices = await edge_tts.list_voices()
    result = []
    for v in all_voices:
        if lang and not v["Locale"].lower().startswith(lang.lower()):
            continue
        result.append({"name": v["ShortName"], "gender": v.get("Gender", "")})
    return result
