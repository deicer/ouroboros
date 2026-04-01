"""TTS tools: send_voice_message(text, voice) — generate speech via edge-tts
and send as Telegram voice message."""

from __future__ import annotations

import logging
from typing import List

from .tts import text_to_speech
from ouroboros.tools.registry import ToolEntry, ToolContext

logger = logging.getLogger(__name__)


async def send_voice_message(ctx: ToolContext, **kwargs) -> str:
    """Generate speech from text using edge-tts and send as a voice message."""
    text = kwargs.get("text") or kwargs.get("message", "")
    voice = kwargs.get("voice", "ru-RU-DmitryNeural")
    reply_to = kwargs.get("reply_to_msg_id")

    if not text:
        return "Error: no text provided for voice message"

    try:
        # Generate audio
        audio_bytes = await text_to_speech(text, voice)
        if not audio_bytes:
            return "Error: text_to_speech returned empty audio data"

        # Get the client (telethon) and target chat
        client = getattr(ctx, "telegram_client", None)
        chat_id = getattr(ctx, "chat_id", None)

        if client is None or chat_id is None:
            return "Error: telegram_client or chat_id not available in context"

        # Send voice message via telethon
        import io
        voice_file = io.BytesIO(audio_bytes)
        voice_file.name = "voice.ogg"

        await client.send_file(
            chat_id,
            file=voice_file,
            voice_note=True,
            reply_to=reply_to,
        )

        return "Voice message sent successfully"

    except Exception as e:
        logger.error(f"send_voice_message error: {e}")
        return f"Error sending voice message: {e}"


async def tts_list_voices(ctx: ToolContext, **kwargs) -> str:
    """List available TTS voices for a specific language."""
    from .tts import list_voices
    lang = kwargs.get("lang", "ru")
    try:
        voices = await list_voices(lang)
        if not voices:
            return f"No voices found for language '{lang}'"
        lines = []
        for v in voices[:20]:  # limit output
            lines.append(f"- {v['name']} ({v['gender']})")
        return "Available voices:\n" + "\n".join(lines)
    except Exception as e:
        logger.error(f"list_voices error: {e}")
        return f"Error listing voices: {e}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="send_voice_message",
            description="Generate voice message from text using edge-tts and send via Telegram",
            parameters={
                "text": "Text to convert to speech",
                "voice": "Voice name (default: ru-RU-DmitryNeural)",
                "reply_to_msg_id": "Optional: message ID to reply to",
            },
            async_function=send_voice_message,
        ),
        ToolEntry(
            name="tts_list_voices",
            description="List available TTS voices for a language (default: ru)",
            parameters={
                "lang": "Language code (default: ru)",
            },
            async_function=tts_list_voices,
        ),
    ]
