# Backend

FastAPI application boundary. Domain modules are separated so realtime transport, AI orchestration, sessions, IELTS flows, evaluation, persistence, and API schemas can evolve independently.

The `/ws/conversation` endpoint now prefers Gemini Live (`gemini-3.8-live`): the browser streams PCM frames to FastAPI, FastAPI maintains the bidirectional Live WebSocket, and native audio frames return through the browser queue. Input/output transcriptions are emitted for persistence and evaluation. Gemini credentials stay server-side.

The existing WebM → transcribe → LLM → TTS pipeline remains available as the `legacy` fallback. Set `REALTIME_PROVIDER=legacy` to disable Live entirely; Live connection failures automatically use `REALTIME_FALLBACK_PROVIDER` (default `legacy`). IELTS remains on the deterministic legacy examiner pipeline until that state machine is migrated independently.

IELTS evaluation is exposed through `/api/conversations/{conversation_id}/evaluation`. It combines deterministic transcript metrics with replaceable LLM and pronunciation services. Coach practice attempts are created with `/api/evaluations/{evaluation_id}/practice` and use the existing `/ws/conversation` transport with `mode=practice`.
