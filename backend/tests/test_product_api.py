from uuid import uuid4

from fastapi.testclient import TestClient

from app import main


def test_auth_conversation_history_and_profile_flow() -> None:
    email = f"user-{uuid4()}@example.com"
    with TestClient(main.app) as client:
        signup = client.post(
            "/api/auth/signup",
            json={"email": email, "password": "password123", "display_name": "Test User"},
        )
        assert signup.status_code == 201
        assert signup.json()["email"] == email

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["display_name"] == "Test User"

        profile = client.patch(
            "/api/auth/me",
            json={"voice": "Kore", "speaking_style": "friendly", "auto_start": False},
        )
        assert profile.status_code == 200
        assert profile.json()["preferences"]["auto_start"] is False

        created = client.post("/api/conversations", json={"topic": "Travel"})
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        history = client.get("/api/conversations")
        assert history.status_code == 200
        assert history.json()[0]["id"] == conversation_id

        ended = client.post(
            f"/api/conversations/{conversation_id}/end", json={"duration_seconds": 90}
        )
        assert ended.status_code == 200
        assert ended.json()["duration_seconds"] == 90
        assert "Travel" in ended.json()["summary"]

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401


def test_protected_routes_reject_anonymous_requests() -> None:
    with TestClient(main.app) as client:
        assert client.get("/api/conversations").status_code == 401
        assert client.post("/api/conversations", json={"topic": "Travel"}).status_code == 401