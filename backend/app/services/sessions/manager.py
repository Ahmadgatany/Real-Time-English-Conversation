from dataclasses import dataclass, field
import time
from uuid import uuid4


@dataclass
class ConversationSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    messages: list[dict[str, str]] = field(default_factory=list)
    topic: str | None = None
    ending_detected: bool = False
    last_assistant_response: str = ""
    last_seen: float = field(default_factory=time.monotonic)

    def add_message(self, role: str, content: str) -> None:
        self.last_seen = time.monotonic()
        self.messages.append({"role": role, "content": content})


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}

    def create(self) -> ConversationSession:
        session = ConversationSession()
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ConversationSession | None:
        session = self._sessions.get(session_id)
        if session and time.monotonic() - session.last_seen > 30 * 60:
            self.remove(session_id)
            return None
        return session

    def cleanup(self, max_age_seconds: int = 30 * 60) -> None:
        now = time.monotonic()
        for session_id, session in list(self._sessions.items()):
            if now - session.last_seen > max_age_seconds:
                self.remove(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)