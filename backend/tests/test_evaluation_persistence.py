import asyncio

from app.core.config import Settings
from app.db.database import create_database, init_database
from app.db.models import Conversation, EvaluationRecord, PracticeAttempt, User


def test_evaluation_and_practice_attempt_persist(tmp_path) -> None:
    async def scenario() -> None:
        settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'evaluation.db'}")
        engine, factory = create_database(settings)
        await init_database(engine)
        async with factory() as session:
            user = User(email="evaluation@example.com", password_hash="hash", display_name="Evaluation")
            session.add(user)
            await session.flush()
            conversation = Conversation(user_id=user.id, topic="IELTS Speaking", mode="ielts")
            session.add(conversation)
            await session.flush()
            practice_conversation = Conversation(user_id=user.id, topic="Coach practice", mode="practice")
            session.add(practice_conversation)
            await session.flush()
            evaluation = EvaluationRecord(
                conversation_id=conversation.id,
                scores={"overall": 6.0},
                metrics={"speech": {"word_count": 20}},
                feedback={"strengths": ["clear ideas"]},
                recommendations=["Add examples"],
                coach_exercises=[{"skill": "fluency", "prompt": "Speak", "target_duration_seconds": 60}],
            )
            session.add(evaluation)
            await session.flush()
            practice = PracticeAttempt(
                evaluation_id=evaluation.id,
                conversation_id=practice_conversation.id,
                user_id=user.id,
                skill="fluency",
                prompt="Speak for one minute",
            )
            session.add(practice)
            await session.commit()
            assert evaluation.id and practice.id
        await engine.dispose()

    asyncio.run(scenario())