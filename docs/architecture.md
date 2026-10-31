# Architecture

The workspace is intentionally split into frontend, backend, shared contracts, tests, documentation, and infrastructure.

The backend separates transport (`api`) from application orchestration (`services`) and persistence (`db`). Domain-specific service boundaries are reserved for realtime audio, AI providers, sessions, IELTS flows, and evaluation.

## Realtime transport

General and practice sessions use a FastAPI WebSocket as a server-side Gemini Live bridge. The browser sends 16-bit PCM frames; FastAPI forwards them to `gemini-3.8-live` and relays native audio frames to the browser queue. Redis holds short-lived connection state for recovery/observability while PostgreSQL-compatible persistence records completed transcript turns asynchronously. The Gemini API key is never exposed to the browser.

The previous turn-based WebM pipeline is retained as an operational fallback (`REALTIME_PROVIDER=legacy`) and is also used by the current deterministic IELTS state machine. This makes the realtime migration reversible during latency and reliability comparison.

This document will be expanded during the implementation phases.
