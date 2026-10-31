"""A small, provider-isolated Gemini Live WebSocket client.

The browser never receives the Gemini key.  Keeping this bridge behind FastAPI
also makes it portable to any container/WebSocket-capable cloud platform.
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import Settings
from app.services.ai.providers import GeminiProviderError


class GeminiLiveSession:
    def __init__(self, settings: Settings, *, system_instruction: str) -> None:
        self.settings = settings
        self.system_instruction = system_instruction
        self._socket: Any | None = None

    async def connect(self) -> None:
        if not self.settings.gemini_api_key:
            raise GeminiProviderError("GEMINI_API_KEY is not configured")
        try:
            from websockets.asyncio.client import connect
        except ImportError as error:  # pragma: no cover - deployment dependency guard
            raise GeminiProviderError("The websockets package is required for Gemini Live.") from error
        endpoint = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={self.settings.gemini_api_key}"
        )
        self._socket = await connect(endpoint, open_timeout=self.settings.gemini_timeout_seconds)
        await self._socket.send(json.dumps({"setup": {
            "model": f"models/{self.settings.gemini_live_model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                    "voiceName": self.settings.gemini_live_voice,
                }}},
            },
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
        }}))
        # A setupComplete message is required before realtime input.
        setup = json.loads(await self._socket.recv())
        if "setupComplete" not in setup:
            await self.close()
            raise GeminiProviderError("Gemini Live did not accept the session setup.")

    async def start_activity(self) -> None:
        await self._send({"realtimeInput": {"activityStart": {}}})

    async def send_audio(self, pcm: bytes) -> None:
        await self._send({"realtimeInput": {"audio": {
            "data": base64.b64encode(pcm).decode("ascii"),
            "mimeType": "audio/pcm;rate=16000",
        }}})

    async def end_activity(self) -> None:
        await self._send({"realtimeInput": {"activityEnd": {}}})

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._socket is None:
            raise GeminiProviderError("Gemini Live session is not connected.")
        await self._socket.send(json.dumps(payload))

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        if self._socket is None:
            return
        async for raw in self._socket:
            yield json.loads(raw)

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
