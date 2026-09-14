# Architecture

The workspace is intentionally split into frontend, backend, shared contracts, tests, documentation, and infrastructure.

The backend separates transport (`api`) from application orchestration (`services`) and persistence (`db`). Domain-specific service boundaries are reserved for realtime audio, AI providers, sessions, IELTS flows, and evaluation.

This document will be expanded during the implementation phases.
