from __future__ import annotations

import base64
import importlib
import io
import logging
from typing import Any, List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry

logger = logging.getLogger(__name__)


def _load_optional_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"{module_name} is not installed") from exc


def _load_pydub_audio_segment():
    return _load_optional_module("pydub").AudioSegment


def _load_whisper():
    return _load_optional_module("whisper")


def _voice_to_text(ctx: ToolContext, voice_message: bytes, model: str = "tiny") -> str:
    """Расшифровывает голосовое сообщение с помощью Whisper"""
    try:
        audio_segment = _load_pydub_audio_segment()
        whisper = _load_whisper()

        if not hasattr(_voice_to_text, "model_instance"):
            _voice_to_text.model_instance = whisper.load_model(model)

        audio = audio_segment.from_file(io.BytesIO(voice_message))
        wav_io = io.BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)

        result = _voice_to_text.model_instance.transcribe(wav_io, language="ru")
        return result["text"]

    except Exception as e:
        logger.error(f"Ошибка распознавания голоса: {e}")
        return f"Не удалось распознать голосовое сообщение: {str(e)}"


def _telegram_voice_handler(ctx: ToolContext, voice_message: Any) -> str:
    """Обрабатывает голосовое сообщение из Telegram"""
    try:
        if voice_message.voice:
            voice_bytes = ctx.telegram_client.download_file(voice_message.voice)
        elif voice_message.audio:
            voice_bytes = ctx.telegram_client.download_file(voice_message.audio)
        else:
            return "Это не голосовое сообщение"

        return _voice_to_text(ctx, voice_bytes)

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        return f"Не удалось обработать голосовое сообщение: {str(e)}"


def get_tools() -> List[ToolEntry]:
    """Возвращает список инструментов голосовой обработки"""
    return [
        ToolEntry(
            name="voice_to_text",
            schema={
                "name": "voice_to_text",
                "description": "Расшифровывает голосовое сообщение (base64 или URL) с помощью Whisper",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Optional: что делать с голосом"},
                        "voice_base64": {"type": "string", "description": "base64 голосового сообщения"},
                        "voice_url": {"type": "string", "description": "URL голосового сообщения"},
                        "model": {"type": "string", "description": "Модель Whisper (default: tiny)"},
                    },
                },
            },
            handler=lambda ctx, **kwargs: _voice_to_text_handler(ctx, **kwargs),
            is_async=True,
        ),
        ToolEntry(
            name="telegram_voice_handler",
            schema={
                "name": "telegram_voice_handler",
                "description": "Обрабатывает голосовое сообщение из Telegram",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "voice_message": {"type": "object", "description": "Объект голосового сообщения из Telegram"},
                    },
                },
            },
            handler=_telegram_voice_handler,
        ),
    ]


async def _voice_to_text_handler(ctx: ToolContext, **kwargs) -> str:
    """Обработчик голосового текста"""
    try:
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

        return _voice_to_text(ctx, voice_bytes, model)

    except Exception as e:
        logger.error(f"Ошибка голосового текста: {e}")
        return f"Не удалось обработать голосовое сообщение: {str(e)}"
