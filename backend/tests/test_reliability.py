import asyncio

from app.services.sessions.manager import SessionManager
from app.core.observability import Metrics


def test_metrics_snapshot_includes_latency_percentiles() -> None:
    observed = Metrics()
    for value in (0.1, 0.2, 0.3, 0.4, 0.5):
        observed.observe("turn", value)
    summary = observed.snapshot()["timings"]["turn"]
    assert summary == {
        "count": 5,
        "average_seconds": 0.3,
        "min_seconds": 0.1,
        "max_seconds": 0.5,
        "p50_seconds": 0.3,
        "p95_seconds": 0.5,
    }


def test_sessions_expire_when_idle(monkeypatch) -> None:
    monkeypatch.setattr("app.services.sessions.manager.time.monotonic", lambda: 1900.0)
    manager = SessionManager()
    session = manager.create()
    session.last_seen = 0.0
    assert manager.get(session.session_id) is None


def test_session_manager_supports_concurrent_sessions() -> None:
    manager = SessionManager()
    sessions = [manager.create() for _ in range(100)]
    assert len({session.session_id for session in sessions}) == 100
    assert all(manager.get(session.session_id) is session for session in sessions)


def test_cleanup_removes_only_expired_sessions(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("app.services.sessions.manager.time.monotonic", lambda: now[0])
    manager = SessionManager()
    old = manager.create()
    fresh = manager.create()
    old.last_seen = -100
    fresh.last_seen = 0
    now[0] = 10
    manager.cleanup(max_age_seconds=50)
    assert manager.get(old.session_id) is None
    assert manager.get(fresh.session_id) is fresh
