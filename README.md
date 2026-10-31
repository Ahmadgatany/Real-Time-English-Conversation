# Real-Time English Conversation

A scalable workspace for an AI English speaking platform with general conversation and future IELTS speaking simulation modes.

## Workspace

- `frontend/`: Next.js web application.
- `backend/`: FastAPI service and backend domain modules.
- `shared/`: Cross-application configuration and API contracts.
- `tests/`: Cross-cutting and integration test areas.
- `docs/`: Product and architecture documentation.
- `infrastructure/`: Local and deployment infrastructure definitions.

## Phase 3: General Conversation Product

Phase 3 adds authenticated accounts, persistent general conversation history, topic selection, profiles, settings, summaries, feedback, and the existing realtime voice engine. IELTS, scoring, advanced pronunciation evaluation, and progress tracking remain deferred.

### Requirements

- Python 3.11 or newer
- Node.js 20.9 or newer
- An API key for the configured speech and language provider
- A browser that supports `MediaRecorder`, Web Audio, and microphone permissions

### Backend

From the repository root, create and activate a virtual environment:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env -Force
```

The commands use the virtual environment's Python directly, so PowerShell script execution policy does not affect setup. Activation is optional; if you prefer it, run `\.venv\Scripts\Activate.ps1` and then use `python` and `uvicorn` normally.

Put the provider key in `backend/.env` as `GEMINI_API_KEY=...`. The backend reads the other provider, model, CORS, and audio-size settings from the same file. `.env` is ignored by git.

Phase 3 also uses these backend variables:

```env
DATABASE_URL=sqlite+aiosqlite:///./conversation.db
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=replace-with-a-long-random-secret
ACCESS_TOKEN_MINUTES=10080
```

SQLite is the default local database. The schema is bootstrapped automatically at startup or explicitly with `python scripts/migrate.py` from `backend`. Redis is optional for local development and is used as an extension point for temporary realtime state; start it with `docker compose -f ..\infrastructure\docker\docker-compose.yml up -d` from the `backend` directory.

Start the API from the `backend` directory:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000 --env-file .env
```

The health endpoint is available at `http://localhost:8000/health`.

### Frontend

In a second terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

Set `NEXT_PUBLIC_API_URL=http://localhost:8000` and `NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/conversation` in `frontend/.env.local`. Open `http://localhost:3000`, create an account, choose a topic or Free Conversation, and allow microphone access. The Home, Conversation, History, Profile, and Settings views are available after login.

Set `NEXT_PUBLIC_WS_URL` in `frontend/.env.local` when the API is not running at `ws://localhost:8000/ws/conversation`.

Each turn is controlled manually: select **Speak** to begin recording, then select **Finish & respond** when you have completed your answer. Recording never stops because of silence, and the microphone does not reopen automatically after the assistant responds. Backend response behavior can be tuned with `MAX_RESPONSE_CHARS` and `RESPONSE_PAUSE_MS` in `backend/.env`.

### Checks

```powershell
cd backend
python -m pytest -q
cd ..\frontend
npm run lint
npm run build
```

### Phase 3 checks

```powershell
cd backend
python scripts/migrate.py
python -m pytest -q
cd ..\frontend
npm run lint
npm run build
```

## Phase 4: IELTS Speaking Exam

Phase 4 adds IELTS Speaking as a separate mode that reuses the Phase 3 realtime microphone, VAD, WebSocket, TTS, and transcript pipeline. Choose `IELTS Speaking exam` on the home screen to start the examiner flow:

```text
START -> INTRO -> PART_1 -> PART_2 -> PREPARATION -> LONG_TURN -> PART_3 -> FINISHED
```

The examiner is neutral and concise. Part 1 uses a controlled question sequence, Part 2 presents a cue card with preparation and long-turn timers, and Part 3 uses abstract follow-up questions with limited adaptation to the candidate's previous answer. The full transcript and exam metadata are stored on the conversation record for a future evaluation phase.

Phase 4 does not calculate bands, score performance, evaluate pronunciation, or provide coaching. When the final response is delivered, the UI shows `Exam completed. Your evaluation is being prepared...`.

Optional backend configuration:

```env
IELTS_PART1_QUESTIONS=4
IELTS_PART3_QUESTIONS=4
IELTS_PREPARATION_SECONDS=60
IELTS_LONG_TURN_SECONDS=120
```

The mode is selected when creating a conversation with `mode=general` or `mode=ielts`, and the realtime socket receives the same mode as its `mode` query parameter.

## Phase 5: IELTS Evaluation and AI Coach

After a completed IELTS exam, `GET /api/conversations/{conversation_id}/evaluation` creates or returns a structured AI estimate. Deterministic metrics cover word count, speaking rate, fillers, repetitions, vocabulary diversity, sentence complexity, and available pauses. The LLM reviews grammar, lexical choices, and examples; pronunciation is isolated behind a replaceable analyzer interface and is marked unavailable until a phoneme provider is configured.

Reports show fluency, vocabulary, grammar, pronunciation, and overall estimated bands, strengths, weaknesses, examples, and recommendations. Every result is labelled as an AI estimate, not an official IELTS score. Coach exercises target the highest-impact weaknesses. `POST /api/evaluations/{evaluation_id}/practice` starts a normal realtime voice session, and ending that session stores deterministic metrics plus a comparison with the previous attempt.

Phase 5 does not add long-term progress analytics, official scoring, or pronunciation/phoneme evaluation.

## Phase 6: Production, Optimization and Personalization

Production deployments should set `ENVIRONMENT=production`, a strong random `JWT_SECRET`, `SECURE_COOKIES=true`, and `REQUIRE_WEBSOCKET_AUTH=true`. Keep `GEMINI_API_KEY` server-side only; it is never sent to the frontend. Configure `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_RETRIES`, `REQUEST_RATE_LIMIT`, `REQUEST_RATE_WINDOW_SECONDS`, and `LOG_LEVEL` for the deployment size.

`/health` reports database readiness and optional Redis availability. `/metrics` exposes in-process request, realtime, provider, retry, latency, and rate-limit counters. For multiple API workers, forward these values to a shared metrics system or replace the in-process collector with Prometheus/OpenTelemetry. Redis remains optional locally but should be enabled for shared rate limits and distributed session state in production.

Phase 6 adds transparent usage records and estimated cost reporting at `/api/conversations/{conversation_id}/cost` and `/api/progress`. Estimates are planning signals, not provider invoices; update the pricing constants in `app/core/observability.py` when provider pricing changes. The Progress dashboard combines IELTS evaluations and coach attempts, highlights recurring weaknesses, tracks score trends, and recommends the next practice activity.

For production, run behind HTTPS with a reverse proxy that supports WebSocket upgrade, use PostgreSQL instead of SQLite, configure backups and migrations, cap request/audio sizes, and centralize structured logs. Never commit `.env` or expose the API key in browser code or client-visible logs.
