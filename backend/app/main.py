import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.api.auth import router as auth_router
from app.api.conversations import router as conversation_router
from app.api.evaluation import router as evaluation_router
from app.api.progress import router as progress_router
from app.db.database import create_database, init_database
from app.db.models import Conversation
from app.services.ai.providers import GeminiProvider
from app.services.realtime.connection import RealtimeConnection
from app.services.sessions.manager import SessionManager
from app.core.limits import RateLimiter
from app.core.observability import metrics

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
database_engine, session_factory = create_database(settings)
session_manager = SessionManager()
ai_provider = GeminiProvider(settings)


@asynccontextmanager
async def lifespan(application: FastAPI):
	await init_database(database_engine)
	application.state.settings = settings
	application.state.session_factory = session_factory
	application.state.ai_provider = ai_provider
	application.state.rate_limiter = RateLimiter(settings.request_rate_limit, settings.request_rate_window_seconds)
	try:
		import redis.asyncio as redis
		application.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
		await asyncio.wait_for(application.state.redis.ping(), timeout=0.5)
	except Exception:
		application.state.redis = None
	yield
	if application.state.redis:
		await application.state.redis.aclose()
	await database_engine.dispose()


app = FastAPI(title="Real-Time English Conversation API", lifespan=lifespan)
app.add_middleware(
	CORSMiddleware,
	allow_origins=settings.cors_origins,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(conversation_router)
app.include_router(evaluation_router)
app.include_router(progress_router)


@app.middleware("http")
async def request_limits(request, call_next):
	if request.url.path not in {"/health", "/metrics"}:
		limiter: RateLimiter = request.app.state.rate_limiter
		if not limiter.allow(request.client.host if request.client else "unknown"):
			metrics.increment("http.rate_limited")
			return JSONResponse({"detail": "Too many requests"}, status_code=429)
	response = await call_next(request)
	metrics.increment(f"http.{request.method}.{response.status_code}")
	return response


@app.get("/health")
async def health() -> dict[str, str]:
	redis_ok = app.state.redis is not None
	return {"status": "ok", "database": "ok", "redis": "ok" if redis_ok else "optional-unavailable"}


@app.get("/metrics")
async def metrics_endpoint() -> dict:
	return metrics.snapshot()


@app.websocket("/ws/conversation")
async def conversation_socket(websocket: WebSocket) -> None:
	limiter: RateLimiter = websocket.app.state.rate_limiter
	if not limiter.allow(websocket.client.host if websocket.client else "unknown"):
		await websocket.close(code=4429)
		return
	conversation = None
	token = websocket.cookies.get("access_token")
	conversation_id = websocket.query_params.get("conversation_id")
	if token and conversation_id:
		user_id = decode_access_token(token, settings)
		if user_id:
			async with session_factory() as session:
				conversation = await session.scalar(
					select(Conversation).where(
						Conversation.id == conversation_id,
						Conversation.user_id == user_id,
					).options(selectinload(Conversation.messages))
				)
	if conversation_id and conversation is None:
		await websocket.close(code=4403)
		return
	if settings.require_websocket_auth and conversation is None:
		await websocket.close(code=4401)
		return
	connection = RealtimeConnection(
		websocket,
		session_manager,
		ai_provider,
		mode=websocket.query_params.get("mode", "general"),
		voice_mode=websocket.query_params.get("voice", "gemini"),
		persistent_conversation=conversation,
		session_factory=session_factory,
		redis=websocket.app.state.redis,
	)
	await connection.run()
