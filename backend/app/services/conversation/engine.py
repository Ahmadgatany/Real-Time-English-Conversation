from dataclasses import dataclass
from collections.abc import AsyncIterator
import re

from app.core.config import Settings
from app.services.ai.providers import LanguageModel
from app.services.sessions.manager import ConversationSession


@dataclass(frozen=True)
class ConversationReply:
    text: str
    is_ending: bool = False


class ConversationEngine:
    """Owns conversational behavior while transport remains responsible for bytes."""

    def __init__(self, settings: Settings, language_model: LanguageModel) -> None:
        self.settings = settings
        self.language_model = language_model

    async def reply(self, session: ConversationSession, transcript: str) -> ConversationReply:
        chunks: list[str] = []
        async for chunk in self.reply_stream(session, transcript):
            chunks.append(chunk)
        response = session.last_assistant_response or "".join(chunks)
        return ConversationReply(response, session.ending_detected)

    async def reply_stream(self, session: ConversationSession, transcript: str) -> AsyncIterator[str]:
        clean_transcript = " ".join(transcript.split())
        if not clean_transcript:
            return
        session.add_message("user", clean_transcript)
        prompt_messages = self._prompt_messages(session)
        stream_method = getattr(self.language_model, "respond_stream", None)
        chunks: list[str] = []
        if stream_method is not None:
            async for chunk in stream_method(prompt_messages):
                chunks.append(chunk)
                yield chunk
            response = "".join(chunks)
        else:
            response = await self.language_model.respond(prompt_messages)
            yield response
        response = self._normalize_response(response, session.last_assistant_response)
        session.add_message("assistant", response)
        session.last_assistant_response = response
        session.topic = self._infer_topic(clean_transcript, session.topic)
        session.ending_detected = self._is_ending(clean_transcript)

    def _prompt_messages(self, session: ConversationSession) -> list[dict[str, str]]:
        behavior = (
            "You are a natural, warm English conversation partner, not a tutor or chatbot. "
            "Reply in one or two short spoken sentences, usually under 35 words. "
            "React to the user's actual details and continue the current topic. Ask one specific follow-up "
            "when it fits. Use occasional brief backchannels such as 'Yeah' or 'Mm-hmm' only when natural. "
            "Do not repeat questions, greetings, or information already said. Avoid lists, explanations, and filler. "
            "If the user is clearly ending the conversation, respond with a warm brief closing."
        )
        recent = session.messages[-12:]
        return [{"role": "system", "content": behavior}, *recent]

    def _normalize_response(self, response: str, previous: str) -> str:
        response = " ".join(response.split()).strip()
        if not response:
            return "Mm-hmm. Tell me more."
        if previous and response.casefold() == previous.casefold():
            return "I hear you. What happened next?"
        if len(response) > self.settings.max_response_chars:
            response = response[: self.settings.max_response_chars].rsplit(" ", 1)[0]
            response = response.rstrip(" ,;:") + "."
        return response

    @staticmethod
    def _infer_topic(transcript: str, previous: str | None) -> str | None:
        words = re.findall(r"[A-Za-z]{4,}", transcript.lower())
        return " ".join(words[:4]) if words else previous

    @staticmethod
    def _is_ending(transcript: str) -> bool:
        return bool(re.search(r"\b(bye|goodbye|see you|gotta go|have to go|that is all)\b", transcript.lower()))