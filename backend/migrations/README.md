# Database migrations

Phase 3 uses SQLAlchemy metadata bootstrap for local development. Run `python scripts/migrate.py` from `backend` after changing models. The application also creates missing tables during startup.

A versioned Alembic history can be introduced when the schema needs production rollout management; the persistence boundary is already isolated in `app/db`.
