from collections.abc import AsyncIterator
import asyncio

from app.core.config import Settings
from app.services.conversation.engine import ConversationEngine
from app.services.sessions.manager import ConversationSession


class FakeLanguageModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.received: list[dict[str, str]] = []

    async def respond(self, messages: list[dict[str, str]]) -> str:
        self.received = messages
        return self.response


async def unused_audio_stream() -> AsyncIterator[bytes]:
    yield b""


def test_engine_retains_context_and_continues_topic() -> None:
    model = FakeLanguageModel("That sounds exciting. What part are you looking forward to?")
    session = ConversationSession()
    engine = ConversationEngine(Settings(), model)

    reply = asyncio.run(engine.reply(session, "I am planning a trip to Japan"))

    assert reply.text.startswith("That sounds exciting")
    assert session.messages == [
        {"role": "user", "content": "I am planning a trip to Japan"},
        {"role": "assistant", "content": reply.text},
    ]
    assert session.topic == "planning trip japan"
    assert model.received[0]["role"] == "system"


def test_engine_limits_long_responses_and_avoids_repetition() -> None:
    model = FakeLanguageModel("one two three four five six seven")
    settings = Settings(max_response_chars=18)
    session = ConversationSession(last_assistant_response="one two three")
    engine = ConversationEngine(settings, model)
    previous_response = session.last_assistant_response

    reply = asyncio.run(engine.reply(session, "Tell me something"))

    assert len(reply.text) <= settings.max_response_chars + 1
    assert reply.text != previous_response


def test_engine_detects_natural_conversation_end() -> None:
    model = FakeLanguageModel("It was lovely talking with you. Bye!")
    session = ConversationSession()
    engine = ConversationEngine(Settings(), model)

    reply = asyncio.run(engine.reply(session, "I have to go now, goodbye"))

    assert reply.is_ending is True