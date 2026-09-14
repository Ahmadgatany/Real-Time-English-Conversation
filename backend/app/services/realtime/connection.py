import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.ai.providers import GeminiProvider, GeminiProviderError
from app.services.conversation.engine import ConversationEngine
from app.services.ielts.engine import IELTSConfig, IELTSExamSession, InvalidExamTransition
from app.services.sessions.manager import ConversationSession, SessionManager
from app.db.models import Conversation, Message
from app.db.models import UsageRecord
from app.core.observability import cost_estimate, metrics

logger = logging.getLogger(__name__)


class RealtimeConnection:
    def __init__(
        self,
        websocket: WebSocket,
        session_manager: SessionManager,
        ai_provider: GeminiProvider,
        mode: str = "general",
        voice_mode: str = "gemini",
        conversation_engine: ConversationEngine | None = None,
        persistent_conversation: Conversation | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.websocket = websocket
        self.session_manager = session_manager
        self.ai_provider = ai_provider
        self.mode = mode
        self.voice_mode = voice_mode if voice_mode in {"gemini", "browser"} else "gemini"
        self.settings: Settings = ai_provider.settings
        self.conversation_engine = conversation_engine or ConversationEngine(self.settings, ai_provider)
        self.ielts_exam = IELTSExamSession(IELTSConfig(
            part1_questions=self.settings.ielts_part1_questions,
            part3_questions=self.settings.ielts_part3_questions,
            preparation_seconds=self.settings.ielts_preparation_seconds,
            long_turn_seconds=self.settings.ielts_long_turn_seconds,
        )) if mode == "ielts" else None
        self.persistent_conversation = persistent_conversation
        self.session_factory = session_factory
        self.session: ConversationSession | None = None
        self.audio_buffer = bytearray()
        self.content_type = "audio/webm"
        self.processing_task: asyncio.Task[None] | None = None
        self.exam_timer_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        await self.websocket.accept()
        self.session = self.session_manager.create()
        if self.persistent_conversation and self.persistent_conversation.messages:
            for message in sorted(self.persistent_conversation.messages, key=lambda item: item.sequence):
                self.session.add_message(message.role, message.text)
            logger.info("Recovered realtime session messages=%d", len(self.session.messages))
        await self.websocket.send_json({
            "type": "session_ready",
            "sessionId": self.session.session_id,
            "voiceMode": self.voice_mode,
        })
        if self.ielts_exam:
            await self._start_ielts_exam()
            self.exam_timer_task = asyncio.create_task(self._run_exam_timer())
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("bytes") is not None:
                    self.audio_buffer.extend(message["bytes"])
                    if len(self.audio_buffer) > self.settings.max_audio_bytes:
                        await self._send_error("Audio utterance is too large")
                        self.audio_buffer.clear()
                elif message.get("text") is not None:
                    await self._handle_event(json.loads(message["text"]))
        except WebSocketDisconnect:
            logger.info("Conversation disconnected: %s", self.session.session_id if self.session else "unknown")
        except Exception:
            logger.exception("Realtime conversation failed")
            await self._send_error("The conversation could not be completed")
        finally:
            await self._cancel_processing(send_event=False)
            if self.exam_timer_task:
                self.exam_timer_task.cancel()
                await asyncio.gather(self.exam_timer_task, return_exceptions=True)
            if self.session:
                self.session_manager.remove(self.session.session_id)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "audio_start":
            await self._cancel_processing(send_event=True)
            self.audio_buffer.clear()
            self.content_type = event.get("contentType", "audio/webm")
        elif event_type == "audio_end":
            if self.processing_task is None or self.processing_task.done():
                self.processing_task = asyncio.create_task(self._process_utterance())
        elif event_type == "close":
            await self.websocket.close()

    async def _process_utterance(self) -> None:
        if not self.audio_buffer or not self.session:
            return
        audio = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        try:
            await self.websocket.send_json({"type": "processing"})
            transcript = await self.ai_provider.transcribe(audio, self.content_type)
            metrics.increment("realtime.transcription.turns")
            await self._persist_usage("transcription", stt_seconds=0, characters=0)
            if not transcript:
                await self.websocket.send_json({"type": "ready"})
                return
            await self.websocket.send_json({"type": "transcript", "text": transcript})
            await self._persist_message("user", transcript)
            if self.ielts_exam:
                await self._handle_ielts_transcript(transcript)
                return
            response_chunks: list[str] = []
            async for chunk in self.conversation_engine.reply_stream(self.session, transcript):
                response_chunks.append(chunk)
                await self.websocket.send_json({"type": "assistant_delta", "text": chunk})
            response_text = self.session.last_assistant_response
            if not response_text:
                response_text = "".join(response_chunks).strip()
            await self._persist_usage(
                "llm",
                input_tokens=max(1, len(transcript) // 4),
                output_tokens=max(1, len(response_text) // 4),
            )
            await self._send_assistant_response(
                response_text,
                "conversation_ending" if self.session.ending_detected else "ready",
            )
        except asyncio.CancelledError:
            logger.info("Conversation turn interrupted")
            raise
        except GeminiProviderError as error:
            logger.warning("Gemini provider error (%s): %s", error.status_code, error)
            await self._send_error(str(error))
        except Exception:
            logger.exception("Failed to process utterance")
            await self._send_error("The conversation could not be completed. Please try again.")

    async def _start_ielts_exam(self) -> None:
        assert self.ielts_exam is not None
        turn = self.ielts_exam.start(time.time())
        await self._send_exam_state(turn)
        await self._send_assistant_response(turn.prompt or "", "ready")

    async def _handle_ielts_transcript(self, transcript: str) -> None:
        assert self.ielts_exam is not None
        try:
            turn = self.ielts_exam.handle_candidate(transcript, time.time())
        except InvalidExamTransition as error:
            logger.info("IELTS transition rejected state=%s reason=%s", self.ielts_exam.state, error)
            await self.websocket.send_json({"type": "exam_wait", "message": str(error)})
            await self.websocket.send_json({"type": "ready"})
            return
        await self._send_exam_state(turn)
        if turn.prompt:
            final_event = "exam_finished" if turn.state.value == "FINISHED" else "ready"
            await self._send_assistant_response(turn.prompt, final_event)
        if turn.transition == self.ielts_exam.state.PREPARATION:
            preparation = self.ielts_exam.advance_after_prompt(time.time())
            await self._send_exam_state(preparation)
            await self.websocket.send_json({"type": "ready"})
        if turn.state.value == "FINISHED":
            await self._persist_ielts_metadata()

    async def _run_exam_timer(self) -> None:
        while self.ielts_exam and self.ielts_exam.state.value != "FINISHED":
            await asyncio.sleep(0.25)
            turn = self.ielts_exam.tick(time.time())
            if not turn:
                continue
            await self._send_exam_state(turn)
            if turn.prompt:
                await self._send_assistant_response(turn.prompt, "ready")
            else:
                await self.websocket.send_json({"type": "ready"})

    async def _send_exam_state(self, turn: Any) -> None:
        deadline = turn.deadline_at
        await self.websocket.send_json({
            "type": "exam_state",
            "state": turn.state.value,
            "deadlineAt": int(deadline * 1000) if deadline else None,
            "remainingSeconds": max(0, int(deadline - time.time())) if deadline else None,
        })

    async def _send_assistant_response(self, response_text: str, final_event: str) -> None:
        await self.websocket.send_json({"type": "assistant_text", "text": response_text})
        await self._persist_message("assistant", response_text)
        if self.voice_mode == "browser":
            logger.info("Using browser speech for assistant response chars=%d", len(response_text))
            await self.websocket.send_json({"type": "voice_mode", "mode": "browser"})
            await self.websocket.send_json({"type": final_event})
            return
        await asyncio.sleep(self.settings.response_pause_ms / 1000)
        audio_chunks: list[bytes] = []
        try:
            async for chunk in self.ai_provider.synthesize(response_text):
                audio_chunks.append(chunk)
                logger.info("TTS chunk generated bytes=%d", len(chunk))
        except GeminiProviderError as error:
            logger.warning("Gemini TTS error (%s): %s", error.status_code, error)
            await self.websocket.send_json({"type": "voice_error", "message": "Gemini voice is unavailable right now."})
            await self.websocket.send_json({"type": final_event})
            return
        except Exception:
            logger.exception("Gemini TTS failed")
            await self.websocket.send_json({"type": "voice_error", "message": "Voice playback is unavailable right now."})
            await self.websocket.send_json({"type": final_event})
            return
        audio_size = sum(len(chunk) for chunk in audio_chunks)
        await self._persist_usage("tts", characters=len(response_text))
        logger.info("Sending assistant audio chunks=%d bytes=%d content_type=audio/wav", len(audio_chunks), audio_size)
        await self.websocket.send_json({"type": "audio_start", "mimeType": "audio/wav", "contentType": "audio/wav"})
        for chunk in audio_chunks:
            await self.websocket.send_bytes(chunk)
        await self.websocket.send_json({"type": "audio_end"})
        await self.websocket.send_json({"type": final_event})

    async def _persist_ielts_metadata(self) -> None:
        if not self.persistent_conversation or not self.session_factory or not self.ielts_exam:
            return
        async with self.session_factory() as session:
            conversation = await session.get(Conversation, self.persistent_conversation.id)
            if conversation:
                conversation.mode = "ielts"
                conversation.summary = "IELTS Speaking exam completed."
                conversation.feedback = {"exam": self.ielts_exam.metadata()}
                await session.commit()

    async def _send_error(self, message: str) -> None:
        try:
            await self.websocket.send_json({"type": "error", "message": message})
        except Exception:
            logger.debug("Could not send websocket error", exc_info=True)

    async def _persist_message(self, role: str, text: str) -> None:
        if not self.persistent_conversation or not self.session_factory:
            return
        async with self.session_factory() as session:
            session.add(Message(
                conversation_id=self.persistent_conversation.id,
                role=role,
                text=text,
                sequence=len(self.session.messages) if self.session else 0,
            ))
            await session.commit()

    async def _persist_usage(self, operation: str, *, input_tokens: int = 0, output_tokens: int = 0, characters: int = 0, stt_seconds: int = 0) -> None:
        if not self.persistent_conversation or not self.session_factory:
            return
        estimate = cost_estimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tts_characters=characters if operation == "tts" else 0,
            stt_seconds=stt_seconds,
        )
        async with self.session_factory() as session:
            session.add(UsageRecord(
                user_id=self.persistent_conversation.user_id,
                conversation_id=self.persistent_conversation.id,
                operation=operation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                characters=characters,
                audio_seconds=stt_seconds,
                cost_usd=estimate["total_usd"],
            ))
            await session.commit()

    async def _cancel_processing(self, send_event: bool) -> None:
        task = self.processing_task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.processing_task = None
        if send_event:
            await self.websocket.send_json({"type": "response_interrupted"})