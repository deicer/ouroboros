import base64
import io
import logging
from typing import Optional, List
from pydub import AudioSegment
from pydub.silence import split_on_silence
import whisper
import telethon
from telethon.tl.types import Message, DocumentAttributeAudio
from ouroboros.tools.registry import ToolEntry, ToolContext

logger = logging.getLogger(__name__)


def _voice_to_text(ctx: ToolContext, voice_message: bytes, model: str = "tiny") -> str:
    """Расшифровывает голосовое сообщение с помощью Whisper"""
    try:
        # Загрузить модель Whisper
        if not hasattr(_voice_to_text, "model_instance"):
            _voice_to_text.model_instance = whisper.load_model(model)

        # Конвертировать голос в формат, совместимый с Whisper
        audio = AudioSegment.from_file(io.BytesIO(voice_message))

        # Whisper работает с файлами формата wav
        wav_io = io.BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)

        # Распознать речь
        result = _voice_to_text.model_instance.transcribe(wav_io, language="ru")
        return result["text"]

    except Exception as e:
        logger.error(f"Ошибка распознавания голоса: {e}")
        return f"Не удалось распознать голосовое сообщение: {str(e)}"


def _telegram_voice_handler(ctx: ToolContext, voice_message: Message) -> str:
    """Обрабатывает голосовое сообщение из Telegram"""
    try:
        # Получить голосовое сообщение
        if voice_message.voice:
            # Голосовое сообщение
            voice_bytes = ctx.telegram_client.download_file(voice_message.voice)
        elif voice_message.audio:
            # Аудиофайл
            voice_bytes = ctx.telegram_client.download_file(voice_message.audio)
        else:
            return "Это не голосовое сообщение"

        # Расшифровать голос
        return _voice_to_text(ctx, voice_bytes)

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        return f"Не удалось обработать голосовое сообщение: {str(e)}"


def get_tools() -> List[ToolEntry]:
    """Возвращает список инструментов голосовой обработки"""
    return [
        ToolEntry(
            name="voice_to_text",
            description="Расшифровывает голосовое сообщение (base64 или URL) с помощью Whisper",
            parameters={
                "prompt": "Optional: что делать с голосом",
                "voice_base64": "base64 голосового сообщения",
                "voice_url": "URL голосового сообщения",
                "model": "Модель Whisper (default: tiny)",
            },
            async_function=lambda ctx, **kwargs: _voice_to_text_handler(ctx, **kwargs),
        ),
        ToolEntry(
            name="telegram_voice_handler",
            description="Обрабатывает голосовое сообщение из Telegram",
            parameters={"voice_message": "Объект голосового сообщения из Telegram"},
            async_function=_telegram_voice_handler,
        ),
    ]


async def _voice_to_text_handler(ctx: ToolContext, **kwargs) -> str:
    """Обработчик голосового текста"""
    try:
        # Получить голосовое сообщение
        voice_bytes = None
        model = kwargs.get("model", "tiny")

        if "voice_base64" in kwargs:
            voice_bytes = base64.b64decode(kwargs["voice_base64"])
        elif "voice_url" in kwargs:
            async with ctx.http_session.get(kwargs["voice_url"]) as response:
                voice_bytes = await response.read()
        else:
            return "Не указано голосовое сообщение (voice_base64 или voice_url)"

        if not voice_bytes:
            return "Не удалось загрузить голосовое сообщение"

        # Расшифровать
        return _voice_to_text(ctx, voice_bytes, model)

    except Exception as e:
        logger.error(f"Ошибка голосового текста: {e}")
        return f"Не удалось обработать голосовое сообщение: {str(e)}"
