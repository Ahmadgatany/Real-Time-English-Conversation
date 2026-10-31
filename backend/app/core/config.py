from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Kore"
    realtime_provider: str = "live"
    realtime_fallback_provider: str = "legacy"
    gemini_live_model: str = "gemini-3.8-live"
    gemini_transcribe_model: str = "gemini-3.5-transcribe-live"
    gemini_live_voice: str = "Kore"
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)
    max_audio_bytes: int = 15 * 1024 * 1024
    max_response_chars: int = 240
    response_pause_ms: int = 240
    vad_silence_ms: int = 900
    vad_threshold: float = 7.0
    interruption_threshold: float = 9.0
    ielts_part1_questions: int = 4
    ielts_part3_questions: int = 4
    ielts_preparation_seconds: int = 60
    ielts_long_turn_seconds: int = 120
    database_url: str = "sqlite+aiosqlite:///./conversation.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-in-local-env-with-at-least-32-bytes"
    access_token_minutes: int = 60 * 24 * 7
    environment: str = "development"
    secure_cookies: bool = False
    require_websocket_auth: bool = False
    request_rate_limit: int = 120
    request_rate_window_seconds: int = 60
    gemini_timeout_seconds: float = 60.0
    gemini_max_retries: int = 2
    log_level: str = "INFO"


def get_settings() -> Settings:
    environment = os.getenv("ENVIRONMENT", "development")
    default_secret = "change-me-in-local-env-with-at-least-32-bytes"
    jwt_secret = os.getenv("JWT_SECRET", default_secret)
    if environment == "production" and jwt_secret == default_secret:
        raise RuntimeError("JWT_SECRET must be configured in production")
    origins = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    )
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
        gemini_tts_model=os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"),
        gemini_tts_voice=os.getenv("GEMINI_TTS_VOICE", "Kore"),
        realtime_provider=os.getenv("REALTIME_PROVIDER", "live").lower(),
        realtime_fallback_provider=os.getenv("REALTIME_FALLBACK_PROVIDER", "legacy").lower(),
        gemini_live_model=os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
        gemini_transcribe_model=os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live"),
        gemini_live_voice=os.getenv("GEMINI_LIVE_VOICE", os.getenv("GEMINI_TTS_VOICE", "Kore")),
        cors_origins=origins or ("http://localhost:3000",),
        max_audio_bytes=int(os.getenv("MAX_AUDIO_BYTES", str(15 * 1024 * 1024))),
        max_response_chars=int(os.getenv("MAX_RESPONSE_CHARS", "240")),
        response_pause_ms=int(os.getenv("RESPONSE_PAUSE_MS", "240")),
        vad_silence_ms=int(os.getenv("VAD_SILENCE_MS", "900")),
        vad_threshold=float(os.getenv("VAD_THRESHOLD", "7")),
        interruption_threshold=float(os.getenv("INTERRUPTION_THRESHOLD", "9")),
        ielts_part1_questions=int(os.getenv("IELTS_PART1_QUESTIONS", "4")),
        ielts_part3_questions=int(os.getenv("IELTS_PART3_QUESTIONS", "4")),
        ielts_preparation_seconds=int(os.getenv("IELTS_PREPARATION_SECONDS", "60")),
        ielts_long_turn_seconds=int(os.getenv("IELTS_LONG_TURN_SECONDS", "120")),
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./conversation.db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        jwt_secret=jwt_secret,
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", str(60 * 24 * 7))),
        environment=environment,
        secure_cookies=os.getenv("SECURE_COOKIES", "true" if environment == "production" else "false").lower() == "true",
        require_websocket_auth=os.getenv("REQUIRE_WEBSOCKET_AUTH", "false").lower() == "true",
        request_rate_limit=int(os.getenv("REQUEST_RATE_LIMIT", "120")),
        request_rate_window_seconds=int(os.getenv("REQUEST_RATE_WINDOW_SECONDS", "60")),
        gemini_timeout_seconds=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60")),
        gemini_max_retries=int(os.getenv("GEMINI_MAX_RETRIES", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
