import pytest

from app.core.config import get_settings
from app.core.limits import RateLimiter
from app.core.observability import cost_estimate
from app.core.security import create_access_token, decode_access_token
from app.services.personalization.service import build_progress, recommend_next_activity


def test_rate_limiter_blocks_and_expires(monkeypatch) -> None:
    clock = iter([0.0, 0.1, 0.2, 61.0])
    monkeypatch.setattr("app.core.limits.time.monotonic", lambda: next(clock))
    limiter = RateLimiter(2, 60)
    assert limiter.allow("user")
    assert limiter.allow("user")
    assert not limiter.allow("user")
    assert limiter.allow("user")


def test_cost_estimate_has_transparent_components() -> None:
    estimate = cost_estimate(input_tokens=1000, output_tokens=500, tts_characters=100, stt_seconds=60)
    assert estimate["llm_usd"] > 0
    assert estimate["tts_usd"] > 0
    assert estimate["stt_usd"] > 0
    assert estimate["total_usd"] == round(sum(estimate[key] for key in ("llm_usd", "tts_usd", "stt_usd")), 6)


def test_personalization_selects_recurring_weakness() -> None:
    progress = build_progress(
        [{"created_at": "2026-01-01", "scores": {"fluency": 5.0, "grammar": 7.0}}],
        [{"created_at": "2026-01-02", "metrics": {"fluency": 5.5, "grammar": 7.0}}],
    )
    assert progress["recurring_weaknesses"] == ["fluency"]
    assert progress["recommended_next"]["skill"] == "fluency"
    assert progress["trends"]["fluency"]["change"] == 0.5


def test_production_config_rejects_default_secret(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        get_settings()


def test_access_tokens_round_trip() -> None:
    settings = get_settings()
    token = create_access_token("user-id", settings)
    assert decode_access_token(token, settings) == "user-id"
