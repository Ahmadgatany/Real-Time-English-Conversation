# Backend

FastAPI application boundary. Domain modules are separated so realtime transport, AI orchestration, sessions, IELTS flows, evaluation, persistence, and API schemas can evolve independently.

The `/ws/conversation` endpoint accepts WebM audio turns and emits JSON state events, streamed assistant text deltas, and binary WAV response chunks. `ConversationEngine` owns turn behavior, context, response length, repetition avoidance, and natural endings. Provider calls live behind `app/services/ai/providers.py`; replacing ASR, LLM, or TTS does not require changing the WebSocket handler.

IELTS evaluation is exposed through `/api/conversations/{conversation_id}/evaluation`. It combines deterministic transcript metrics with replaceable LLM and pronunciation services. Coach practice attempts are created with `/api/evaluations/{evaluation_id}/practice` and use the existing `/ws/conversation` transport with `mode=practice`.
