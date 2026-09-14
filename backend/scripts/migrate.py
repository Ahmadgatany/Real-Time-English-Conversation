import asyncio

from app.core.config import get_settings
from app.db.database import create_database, init_database


async def main() -> None:
    settings = get_settings()
    engine, _ = create_database(settings)
    await init_database(engine)
    await engine.dispose()
    print("Database schema is ready.")


if __name__ == "__main__":
    asyncio.run(main())
