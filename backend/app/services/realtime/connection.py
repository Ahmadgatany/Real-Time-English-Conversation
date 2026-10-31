import asyncio
import base64
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.ai.providers import GeminiProvider, GeminiProviderError
from app.services.ai.live import GeminiLiveSession
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
        redis: Any | None = None,
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
        self.redis = redis
        self.session: ConversationSession | None = None
        self.audio_buffer = bytearray()
        self.content_type = "audio/webm"
        self.processing_task: asyncio.Task[None] | None = None
        self.exam_timer_task: asyncio.Task[None] | None = None
        self.live_session: GeminiLiveSession | None = None
        self.live_receiver_task: asyncio.Task[None] | None = None
        self.live_audio_open = False
        self.live_audio_chunk_sequence = 0
        self.live_audio_stream_sequence = 0
        self.live_transcript = ""
        self.live_response_text = ""
        self.turn_started_at: float | None = None
        self.turn_started_wall: float | None = None
        self.turn_first_text_at: float | None = None
        self.turn_first_audio_at: float | None = None
        self.turn_first_audio_sent_at: float | None = None
        self.turn_audio_sent_at: float | None = None
        self.turn_audio_generation_complete_at: float | None = None
        self.turn_last_chunk_sent_at: float | None = None
        self.turn_chunk_intervals_ms: list[float] = []
        self.turn_last_gemini_chunk_at: float | None = None
        self.turn_gemini_chunk_intervals_ms: list[float] = []
        self.turn_gemini_started_at: float | None = None
        self.turn_synthesis_started_at: float | None = None

    async def run(self) -> None:
        await self.websocket.accept()
        self.session = self.session_manager.create()
        if self.persistent_conversation and self.persistent_conversation.messages:
            for message in sorted(self.persistent_conversation.messages, key=lambda item: item.sequence):
                self.session.add_message(message.role, message.text)
            logger.info("Recovered realtime session messages=%d", len(self.session.messages))
        transport = "live_pcm" if await self._start_live_if_enabled() else "legacy_turns"
        logger.info("Realtime session ready mode=%s transport=%s", self.mode, transport)
        await self.websocket.send_json({
            "type": "session_ready",
            "sessionId": self.session.session_id,
            "voiceMode": self.voice_mode,
            "transport": transport,
            "provider": "gemini",
            "model": self.settings.gemini_live_model if transport == "live_pcm" else self.settings.gemini_model,
            "streamingMode": "incremental_gemini_live_pcm" if transport == "live_pcm" else "legacy_buffered_turns",
            "latencyConfig": {
                "sampleRateHz": 24_000,
                "channels": 1,
                "pcmFormat": "s16le",
                "responsePauseMs": self.settings.response_pause_ms,
                "fallbackProvider": self.settings.realtime_fallback_provider,
            } if transport == "live_pcm" else None,
        })
        await self._cache_session_state("connected")
        if self.ielts_exam:
            await self._start_ielts_exam()
            self.exam_timer_task = asyncio.create_task(self._run_exam_timer())
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    if self.live_session:
                        await self.live_session.send_audio(message["bytes"])
                    else:
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
            if self.live_receiver_task:
                self.live_receiver_task.cancel()
                await asyncio.gather(self.live_receiver_task, return_exceptions=True)
            if self.live_session:
                await self.live_session.close()
            await self._cache_session_state("disconnected", delete=True)
            if self.session:
                self.session_manager.remove(self.session.session_id)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "audio_start":
            await self._cancel_processing(send_event=True)
            self.audio_buffer.clear()
            self.content_type = event.get("contentType", "audio/webm")
            if self.live_session:
                await self.live_session.start_activity()
                await self._cache_session_state("listening")
        elif event_type == "audio_end":
            self._start_latency_turn()
            if self.live_session:
                self.turn_gemini_started_at = time.perf_counter()
                await self.live_session.end_activity()
                await self._cache_session_state("thinking")
                return
            if self.processing_task is None or self.processing_task.done():
                self.processing_task = asyncio.create_task(self._process_utterance())
        elif event_type == "close":
            await self.websocket.close()

    async def _start_live_if_enabled(self) -> bool:
        # IELTS prompts are deterministic and deliberately stay on the legacy
        # pipeline until its live examiner state machine is migrated separately.
        if self.settings.realtime_provider != "live" or self.mode == "ielts":
            return False
        try:
            self.live_session = GeminiLiveSession(
                self.settings,
                system_instruction=(
                    "You are a warm, concise English conversation partner for an English-learning app. "
                    "Speak and write ONLY English in every response, even when the user speaks another language. "
                    "If the user uses another language, politely invite them to continue in English. "
                    "Respond naturally in short spoken English and let the user interrupt you."
                ),
            )
            await self.live_session.connect()
            self.live_receiver_task = asyncio.create_task(self._receive_live_events())
            return True
        except Exception as error:
            self.live_session = None
            logger.warning("Gemini Live unavailable; using %s fallback: %s", self.settings.realtime_fallback_provider, error)
            metrics.increment("realtime.live.fallbacks")
            return False

    async def _receive_live_events(self) -> None:
        assert self.live_session is not None
        try:
            async for payload in self.live_session.receive():
                content = payload.get("serverContent", {})
                input_transcript = content.get("inputTranscription", {}).get("text", "")
                if input_transcript:
                    self.live_transcript += input_transcript
                    await self.websocket.send_json({"type": "transcript_delta", "text": input_transcript})
                output_transcript = content.get("outputTranscription", {}).get("text", "")
                if output_transcript:
                    self._mark_first_text()
                    self.live_response_text += output_transcript
                    await self.websocket.send_json({"type": "assistant_delta", "text": output_transcript})
                for part in content.get("modelTurn", {}).get("parts", []):
                    inline = part.get("inlineData") or {}
                    if inline.get("data"):
                        mime_type = inline.get("mimeType", "audio/pcm;rate=24000").lower()
                        if not mime_type.startswith("audio/pcm") or ("rate=" in mime_type and "rate=24000" not in mime_type):
                            logger.error("Unsupported Gemini Live audio format mime_type=%s", mime_type)
                            continue
                        received_at = time.perf_counter()
                        pcm = base64.b64decode(inline["data"], validate=True)
                        if not pcm or len(pcm) % 2:
                            logger.warning("Ignoring malformed Gemini Live PCM chunk bytes=%d", len(pcm))
                            continue
                        self._mark_first_audio(received_at)
                        if self.turn_last_gemini_chunk_at is not None:
                            self.turn_gemini_chunk_intervals_ms.append((received_at - self.turn_last_gemini_chunk_at) * 1000)
                        self.turn_last_gemini_chunk_at = received_at
                        if not self.live_audio_open:
                            self.live_audio_open = True
                            self.live_audio_chunk_sequence = 0
                            self.live_audio_stream_sequence += 1
                            await self.websocket.send_json({
                                "type": "audio_start",
                                "streamId": self.live_audio_stream_sequence,
                                "streaming": True,
                                "mimeType": "audio/pcm;rate=24000",
                                "sampleRateHz": 24_000,
                                "channels": 1,
                                "pcmFormat": "s16le",
                            })
                        sequence = self.live_audio_chunk_sequence
                        await self.websocket.send_json({"type": "audio_chunk", "streamId": self.live_audio_stream_sequence, "sequence": sequence})
                        await self.websocket.send_bytes(pcm)
                        self._mark_audio_chunk_sent()
                        self.live_audio_chunk_sequence += 1
                if content.get("interrupted"):
                    if self.live_audio_open:
                        await self._end_live_audio_stream(interrupted=True)
                    self.live_audio_open = False
                    await self.websocket.send_json({"type": "response_interrupted"})
                if content.get("turnComplete"):
                    self.turn_audio_generation_complete_at = time.perf_counter()
                    await self._complete_live_turn()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Gemini Live receive loop failed")
            metrics.increment("realtime.live.failures")
            await self._send_error("Live audio connection was interrupted. Please try again.")

    async def _complete_live_turn(self) -> None:
        if self.live_audio_open:
            await self._end_live_audio_stream()
        transcript, response = self.live_transcript.strip(), self.live_response_text.strip()
        self.live_transcript = ""
        self.live_response_text = ""
        if transcript:
            self.session.add_message("user", transcript) if self.session else None
            await self.websocket.send_json({"type": "transcript", "text": transcript})
            asyncio.create_task(self._persist_message("user", transcript))
        if response:
            self.session.add_message("assistant", response) if self.session else None
            await self.websocket.send_json({"type": "assistant_text", "text": response})
            asyncio.create_task(self._persist_message("assistant", response))
        await self.websocket.send_json({"type": "ready"})
        await self._send_latency_report()
        await self._cache_session_state("ready")

    async def _end_live_audio_stream(self, *, interrupted: bool = False) -> None:
        await self.websocket.send_json({
            "type": "audio_end",
            "streamId": self.live_audio_stream_sequence,
            "interrupted": interrupted,
            "chunkCount": self.live_audio_chunk_sequence,
        })
        self.live_audio_open = False

    async def _cache_session_state(self, state: str, *, delete: bool = False) -> None:
        if not self.redis or not self.session:
            return
        key = f"realtime:session:{self.session.session_id}"
        try:
            if delete:
                await self.redis.delete(key)
            else:
                await self.redis.set(key, json.dumps({"state": state, "mode": self.mode}), ex=1800)
        except Exception:
            logger.debug("Redis realtime state unavailable", exc_info=True)

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
            self.turn_gemini_started_at = time.perf_counter()
            async for chunk in self.conversation_engine.reply_stream(self.session, transcript):
                response_chunks.append(chunk)
                self._mark_first_text()
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
            self.turn_synthesis_started_at = time.perf_counter()
            async for chunk in self.ai_provider.synthesize(response_text):
                self._mark_first_audio()
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
        self._mark_audio_sent()
        await self.websocket.send_json({"type": "audio_end"})
        await self._send_latency_report()
        await self.websocket.send_json({"type": final_event})

    def _start_latency_turn(self) -> None:
        self.turn_started_at = time.perf_counter()
        self.turn_started_wall = time.time()
        self.turn_first_text_at = None
        self.turn_first_audio_at = None
        self.turn_first_audio_sent_at = None
        self.turn_audio_sent_at = None
        self.turn_audio_generation_complete_at = None
        self.turn_last_chunk_sent_at = None
        self.turn_chunk_intervals_ms = []
        self.turn_last_gemini_chunk_at = None
        self.turn_gemini_chunk_intervals_ms = []
        self.turn_gemini_started_at = None
        self.turn_synthesis_started_at = None
        logger.info("realtime.latency event=turn_finalized ts=%.6f", self.turn_started_wall)

    def _mark_first_text(self) -> None:
        if self.turn_started_at is not None and self.turn_first_text_at is None:
            self.turn_first_text_at = time.perf_counter()
            logger.info("realtime.latency event=first_text elapsed_ms=%.2f", (self.turn_first_text_at - self.turn_started_at) * 1000)

    def _mark_first_audio(self, received_at: float | None = None) -> None:
        if self.turn_started_at is not None and self.turn_first_audio_at is None:
            self.turn_first_audio_at = received_at if received_at is not None else time.perf_counter()
            logger.info("realtime.latency event=first_audio_backend elapsed_ms=%.2f", (self.turn_first_audio_at - self.turn_started_at) * 1000)

    def _mark_audio_chunk_sent(self) -> None:
        sent_at = time.perf_counter()
        if self.turn_first_audio_sent_at is None:
            self.turn_first_audio_sent_at = sent_at
            if self.turn_started_at is not None:
                logger.info("realtime.latency event=first_audio_chunk_sent elapsed_ms=%.2f", (sent_at - self.turn_started_at) * 1000)
        if self.turn_last_chunk_sent_at is not None:
            interval_ms = (sent_at - self.turn_last_chunk_sent_at) * 1000
            self.turn_chunk_intervals_ms.append(interval_ms)
            logger.info("realtime.latency event=audio_chunk_interval ms=%.2f", interval_ms)
        self.turn_last_chunk_sent_at = sent_at
        self.turn_audio_sent_at = sent_at

    def _mark_audio_sent(self) -> None:
        self.turn_audio_sent_at = time.perf_counter()

    async def _send_latency_report(self) -> None:
        if self.turn_started_at is None:
            return
        now = time.perf_counter()
        report = {
            "type": "latency_metrics",
            "endOfSpeechToTextMs": round((self.turn_first_text_at - self.turn_started_at) * 1000, 1) if self.turn_first_text_at else None,
            "endOfSpeechToFirstAudioMs": round((self.turn_first_audio_at - self.turn_started_at) * 1000, 1) if self.turn_first_audio_at else None,
            "endOfSpeechToFirstAudioSentMs": round((self.turn_first_audio_sent_at - self.turn_started_at) * 1000, 1) if self.turn_first_audio_sent_at else None,
            "audioGenerationCompleteMs": round((self.turn_audio_generation_complete_at - self.turn_started_at) * 1000, 1) if self.turn_audio_generation_complete_at else None,
            "firstGeminiAudioToGenerationCompleteMs": round((self.turn_audio_generation_complete_at - self.turn_first_audio_at) * 1000, 1) if self.turn_audio_generation_complete_at and self.turn_first_audio_at else None,
            "geminiFirstChunkToBackendFirstSendMs": round((self.turn_first_audio_sent_at - self.turn_first_audio_at) * 1000, 1) if self.turn_first_audio_sent_at and self.turn_first_audio_at else None,
            "lastAudioSendToGenerationCompleteMs": round((self.turn_audio_generation_complete_at - self.turn_audio_sent_at) * 1000, 1) if self.turn_audio_sent_at and self.turn_audio_generation_complete_at else None,
            "backendChunkIntervalsMs": self.turn_chunk_intervals_ms,
            "backendChunkIntervalStatsMs": self._duration_stats(self.turn_chunk_intervals_ms),
            "geminiAudioChunkIntervalsMs": [round(value, 2) for value in self.turn_gemini_chunk_intervals_ms],
            "geminiAudioChunkIntervalStatsMs": self._duration_stats(self.turn_gemini_chunk_intervals_ms),
            "geminiRequestToFirstTextMs": round((self.turn_first_text_at - self.turn_gemini_started_at) * 1000, 1) if self.turn_first_text_at and self.turn_gemini_started_at else None,
            "ttsRequestToFirstAudioMs": round((self.turn_first_audio_at - self.turn_synthesis_started_at) * 1000, 1) if self.turn_first_audio_at and self.turn_synthesis_started_at else None,
            "endOfSpeechToBackendDeliveryMs": round((self.turn_audio_sent_at - self.turn_started_at) * 1000, 1) if self.turn_audio_sent_at else None,
            "backendAudioDeliveryMs": round((self.turn_audio_sent_at - self.turn_first_audio_at) * 1000, 1) if self.turn_audio_sent_at and self.turn_first_audio_at else None,
            "endOfSpeechToBackendCompleteMs": round((now - self.turn_started_at) * 1000, 1),
            "serverTimestamp": time.time(),
        }
        logger.info("realtime.latency summary=%s", json.dumps(report, separators=(",", ":")))
        for key, metric_name in (("endOfSpeechToTextMs", "realtime.latency.ttft"), ("endOfSpeechToFirstAudioMs", "realtime.latency.first_audio"), ("endOfSpeechToFirstAudioSentMs", "realtime.latency.first_audio_sent"), ("endOfSpeechToBackendDeliveryMs", "realtime.latency.backend_delivery"), ("endOfSpeechToBackendCompleteMs", "realtime.latency.backend_complete")):
            value = report[key]
            if value is not None:
                metrics.observe(metric_name, value / 1000)
        await self.websocket.send_json(report)
        self.turn_started_at = None

    @staticmethod
    def _duration_stats(values: list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"count": 0, "average": None, "minimum": None, "maximum": None, "p50": None, "p95": None}
        ordered = sorted(values)
        return {
            "count": len(ordered),
            "average": round(sum(ordered) / len(ordered), 2),
            "minimum": round(ordered[0], 2),
            "maximum": round(ordered[-1], 2),
            "p50": round(ordered[max(0, (len(ordered) + 1) // 2 - 1)], 2),
            "p95": round(ordered[max(0, int(0.95 * len(ordered) + 0.999999) - 1)], 2),
        }

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
            if self.live_audio_open:
                self.live_audio_open = False
                await self.websocket.send_json({"type": "audio_end"})
            await self.websocket.send_json({"type": "response_interrupted"})
