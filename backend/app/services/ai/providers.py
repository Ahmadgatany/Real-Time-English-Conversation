import base64
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import BytesIO
import json
import logging
from typing import Protocol
import wave
import time

import httpx

from app.core.config import Settings
from app.core.observability import cost_estimate, elapsed, metrics

logger = logging.getLogger(__name__)


class GeminiProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, content_type: str) -> str: ...


class LanguageModel(Protocol):
    async def respond(self, messages: list[dict[str, str]]) -> str: ...

    def respond_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


@dataclass
class GeminiProvider:
    settings: Settings

    def _url(self, model: str) -> str:
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    async def _generate(self, model: str, payload: dict) -> dict:
        for attempt in range(self.settings.gemini_max_retries + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.settings.gemini_timeout_seconds) as client:
                    response = await client.post(
                        self._url(model),
                        params={"key": self.settings.gemini_api_key},
                        json=payload,
                    )
                metrics.increment("gemini.generate.requests")
                metrics.observe("gemini.generate.latency", elapsed(started))
                if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                    if attempt < self.settings.gemini_max_retries:
                        metrics.increment("gemini.generate.retries")
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                self._raise_for_status(response)
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                metrics.increment("gemini.generate.failures")
                if attempt >= self.settings.gemini_max_retries:
                    raise GeminiProviderError("Gemini request timed out or the network is unavailable.") from error
                metrics.increment("gemini.generate.retries")
                await asyncio.sleep(min(2 ** attempt, 8))
        raise GeminiProviderError("Gemini is temporarily rate limited.", 429)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        logger.error("Gemini API error status=%d body=%s", response.status_code, response.text[:1000])
        if response.status_code == 429:
            raise GeminiProviderError("Gemini rate limit reached. Please wait and try again.", 429)
        if response.status_code in {500, 502, 503, 504}:
            raise GeminiProviderError("Gemini is temporarily unavailable. Please try again.", response.status_code)
        raise GeminiProviderError("Gemini rejected the request. Check the selected model and API key configuration.", response.status_code)

    @staticmethod
    def _text(response: dict) -> str:
        parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts).strip()

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        mime_type = content_type.split(";", 1)[0].strip().lower() or "audio/webm"
        logger.info("Gemini transcription request bytes=%d content_type=%s", len(audio), mime_type)
        metrics.increment("gemini.transcription.requests")
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Transcribe this audio exactly. Return only the spoken words."},
                    {"inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(audio).decode("ascii"),
                    }},
                ],
            }],
        }
        return self._text(await self._generate(self.settings.gemini_model, payload))

    async def respond(self, messages: list[dict[str, str]]) -> str:
        chunks: list[str] = []
        async for chunk in self.respond_stream(messages):
            chunks.append(chunk)
        return "".join(chunks).strip()

    async def respond_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        payload = {
            "system_instruction": {"parts": [{
                "text": "You are a warm, natural English conversation partner. Keep replies concise and conversational."
            }]},
            "contents": [
                {
                    "role": "model" if message["role"] == "assistant" else "user",
                    "parts": [{"text": message["content"]}],
                }
                for message in messages
            ],
            "generationConfig": {"temperature": 0.7},
        }
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.settings.gemini_model}:streamGenerateContent"
        async with httpx.AsyncClient(timeout=self.settings.gemini_timeout_seconds) as client:
            for attempt in range(self.settings.gemini_max_retries + 1):
                async with client.stream(
                    "POST",
                    url,
                    params={"key": self.settings.gemini_api_key, "alt": "sse"},
                    json=payload,
                ) as response:
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.gemini_max_retries:
                        metrics.increment("gemini.stream.retries")
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                    metrics.increment("gemini.stream.requests")
                    if not response.is_success:
                        await response.aread()
                    self._raise_for_status(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data:
                            text = self._text(json.loads(data))
                            if text:
                                yield text
                    return
        raise GeminiProviderError("Gemini is temporarily rate limited.", 429)

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        logger.info("Gemini TTS request model=%s voice=%s chars=%d", self.settings.gemini_tts_model, self.settings.gemini_tts_voice, len(text))
        metrics.increment("gemini.tts.requests")
        logger.info("Estimated TTS cost=%s", cost_estimate(tts_characters=len(text)))
        payload = {
            "contents": [{"parts": [{"text": f"Say naturally: {text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": self.settings.gemini_tts_voice}
                }},
            },
        }
        response = await self._generate(self.settings.gemini_tts_model, payload)
        parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        audio_part = next((part.get("inlineData") for part in parts if part.get("inlineData")), None)
        if not audio_part or not audio_part.get("data"):
            raise RuntimeError("Gemini TTS returned no audio")
        audio = self._pcm_to_wav(base64.b64decode(audio_part["data"]))
        logger.info("Gemini TTS response bytes=%d content_type=audio/wav", len(audio))
        yield audio

    @staticmethod
    def _pcm_to_wav(pcm: bytes) -> bytes:
        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(pcm)
        return output.getvalue()