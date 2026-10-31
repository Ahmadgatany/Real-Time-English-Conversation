from collections.abc import AsyncIterator
import asyncio

from fastapi.testclient import TestClient

from app import main
from app.core.config import Settings
from app.services.sessions.manager import ConversationSession, SessionManager
from app.services.realtime.connection import RealtimeConnection


class FakeProvider:
    settings = Settings()

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        assert audio == b"audio"
        assert content_type == "audio/webm"
        return "Hello there"

    async def respond(self, messages: list[dict[str, str]]) -> str:
        assert messages[-1] == {"role": "user", "content": "Hello there"}
        return "Nice to meet you."

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        assert text == "Nice to meet you."
        yield b"audio-response"


class FakeWebSocket:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    async def send_json(self, event: dict[str, str]) -> None:
        self.events.append(event)

    async def accept(self) -> None:
        pass

    async def receive(self) -> dict[str, str]:
        return {"type": "websocket.disconnect"}


def test_session_manager_creates_and_removes_sessions() -> None:
    manager = SessionManager()
    session = manager.create()
    assert isinstance(session, ConversationSession)
    assert manager.get(session.session_id) is session
    manager.remove(session.session_id)
    assert manager.get(session.session_id) is None


def test_health_endpoint() -> None:
    with TestClient(main.app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["database"] == "ok"
        assert health["redis"] in {"ok", "optional-unavailable"}


def test_websocket_processes_audio_turn(monkeypatch) -> None:
    monkeypatch.setattr(main, "ai_provider", FakeProvider())
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/conversation") as websocket:
            assert websocket.receive_json()["type"] == "session_ready"
            websocket.send_json({"type": "audio_start", "contentType": "audio/webm"})
            websocket.send_bytes(b"audio")
            websocket.send_json({"type": "audio_end"})

            assert websocket.receive_json()["type"] == "processing"
            assert websocket.receive_json() == {"type": "transcript", "text": "Hello there"}
            assert websocket.receive_json() == {"type": "assistant_delta", "text": "Nice to meet you."}
            assert websocket.receive_json() == {"type": "assistant_text", "text": "Nice to meet you."}
            assert websocket.receive_json() == {
                "type": "audio_start",
                "mimeType": "audio/wav",
                "contentType": "audio/wav",
            }
            assert websocket.receive_bytes() == b"audio-response"
            assert websocket.receive_json()["type"] == "audio_end"
            assert websocket.receive_json()["type"] == "latency_metrics"
            assert websocket.receive_json()["type"] == "ready"


def test_realtime_connection_interrupts_active_response() -> None:
    async def scenario() -> None:
        socket = FakeWebSocket()
        connection = RealtimeConnection(socket, SessionManager(), FakeProvider())
        connection.processing_task = asyncio.create_task(asyncio.sleep(30))

        await connection._cancel_processing(send_event=True)

        assert connection.processing_task is None
        assert socket.events == [{"type": "response_interrupted"}]

    asyncio.run(scenario())


def test_websocket_disconnect_ends_receive_loop_cleanly() -> None:
    async def scenario() -> None:
        connection = RealtimeConnection(FakeWebSocket(), SessionManager(), FakeProvider())
        await connection.run()
        assert connection.session is None

    asyncio.run(scenario())
