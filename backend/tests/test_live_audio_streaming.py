import asyncio
import base64
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings
from app.services.realtime.connection import RealtimeConnection
from app.services.sessions.manager import SessionManager


class FakeProvider:
    settings = Settings()


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(("json", message))

    async def send_bytes(self, message: bytes) -> None:
        self.sent.append(("bytes", message))

    async def accept(self) -> None:
        pass

    async def receive(self) -> dict:
        return {"type": "websocket.disconnect"}


class FakeLiveSession:
    def __init__(self, events: list[dict]) -> None:
        self.events = events

    async def receive(self) -> AsyncIterator[dict]:
        for event in self.events:
            yield event


def audio_event(pcm: bytes) -> dict:
    return {"serverContent": {"modelTurn": {"parts": [{
        "inlineData": {"data": base64.b64encode(pcm).decode("ascii")}
    }]}}}


def test_live_pcm_chunks_are_forwarded_in_order_before_turn_complete() -> None:
    async def scenario() -> list[tuple[str, object]]:
        websocket = FakeWebSocket()
        connection = RealtimeConnection(websocket, SessionManager(), FakeProvider())
        connection.session = connection.session_manager.create()
        connection._start_latency_turn()
        connection.live_session = FakeLiveSession([audio_event(b"\x01\x00"), audio_event(b"\x02\x00"), {"serverContent": {"turnComplete": True}}])
        await connection._receive_live_events()
        return websocket.sent

    sent = asyncio.run(scenario())
    assert [payload for kind, payload in sent if kind == "bytes"] == [b"\x01\x00", b"\x02\x00"]
    types = [payload.get("type") for kind, payload in sent if kind == "json"]
    assert types.count("audio_start") == 1
    assert types.count("audio_chunk") == 2
    assert types.index("audio_start") < types.index("audio_chunk") < types.index("audio_end")
    chunk_events = [payload for kind, payload in sent if kind == "json" and payload.get("type") == "audio_chunk"]
    assert [payload["sequence"] for payload in chunk_events] == [0, 1]
    assert {payload["streamId"] for payload in chunk_events} == {1}
    start_event = next(payload for kind, payload in sent if kind == "json" and payload.get("type") == "audio_start")
    assert (start_event["sampleRateHz"], start_event["channels"], start_event["pcmFormat"]) == (24_000, 1, "s16le")
    report = next(payload for kind, payload in sent if kind == "json" and payload.get("type") == "latency_metrics")
    assert report["endOfSpeechToFirstAudioMs"] is not None
    assert report["endOfSpeechToFirstAudioSentMs"] is not None
    assert report["audioGenerationCompleteMs"] is not None


def test_live_interruption_ends_stream_and_signals_frontend() -> None:
    async def scenario() -> list[tuple[str, object]]:
        websocket = FakeWebSocket()
        connection = RealtimeConnection(websocket, SessionManager(), FakeProvider())
        connection.session = connection.session_manager.create()
        connection._start_latency_turn()
        connection.live_session = FakeLiveSession([audio_event(b"\x01\x00"), {"serverContent": {"interrupted": True}}])
        await connection._receive_live_events()
        return websocket.sent

    sent = asyncio.run(scenario())
    json_events = [payload for kind, payload in sent if kind == "json"]
    assert any(event.get("type") == "audio_end" and event.get("interrupted") for event in json_events)
    assert any(event.get("type") == "response_interrupted" for event in json_events)
    assert not any(event.get("type") == "latency_metrics" for event in json_events)


def test_live_receiver_propagates_cancellation() -> None:
    class WaitingLiveSession:
        async def receive(self) -> AsyncIterator[dict]:
            await asyncio.Event().wait()
            yield {}

    async def scenario() -> None:
        connection = RealtimeConnection(FakeWebSocket(), SessionManager(), FakeProvider())
        connection.live_session = WaitingLiveSession()
        task = asyncio.create_task(connection._receive_live_events())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_disconnect_does_not_issue_another_websocket_receive() -> None:
    class DisconnectSocket(FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.receive_count = 0

        async def receive(self) -> dict:
            self.receive_count += 1
            if self.receive_count == 1:
                return {"type": "websocket.disconnect"}
            raise AssertionError("receive called again after disconnect")

    async def scenario() -> int:
        socket = DisconnectSocket()
        await RealtimeConnection(socket, SessionManager(), FakeProvider()).run()
        return socket.receive_count

    assert asyncio.run(scenario()) == 1
