from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import current_user, db_session
from app.api.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOutput,
    EndConversationInput,
    MessageOutput,
    TopicOutput,
)
from app.db.models import Conversation, Message, SessionStat, User
from app.db.models import PracticeAttempt
from app.services.evaluation.engine import EvaluationEngine

router = APIRouter(prefix="/api", tags=["conversations"])

TOPICS = [
    {"id": "daily-life", "title": "Daily life", "description": "Talk about routines, habits, and small wins.", "prompt": "What has a normal day looked like for you recently?"},
    {"id": "travel", "title": "Travel", "description": "Share places, plans, and memorable moments.", "prompt": "Where would you love to travel next, and why?"},
    {"id": "work-study", "title": "Work or study", "description": "Discuss goals, challenges, and what keeps you curious.", "prompt": "What are you working on or learning these days?"},
    {"id": "food-culture", "title": "Food and culture", "description": "Compare tastes, traditions, and favorite places.", "prompt": "What food reminds you of home?"},
]


@router.get("/topics", response_model=list[TopicOutput])
async def topics() -> list[dict[str, str]]:
    return TOPICS


@router.post("/conversations", response_model=ConversationOutput, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Conversation:
    conversation = Conversation(
        user_id=user.id,
        topic=payload.topic.strip() or "Free Conversation",
        mode=payload.mode,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.get("/conversations", response_model=list[ConversationOutput])
async def list_conversations(
    user: User = Depends(current_user), session: AsyncSession = Depends(db_session)
) -> list[Conversation]:
    result = await session.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(desc(Conversation.started_at))
    )
    return list(result)


async def owned_conversation(conversation_id: str, user: User, session: AsyncSession) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .options(selectinload(Conversation.messages), selectinload(Conversation.statistics))
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Conversation:
    return await owned_conversation(conversation_id, user, session)


@router.post("/conversations/{conversation_id}/end", response_model=ConversationDetail)
async def end_conversation(
    conversation_id: str,
    payload: EndConversationInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Conversation:
    conversation = await owned_conversation(conversation_id, user, session)
    conversation.ended_at = datetime.now(timezone.utc)
    conversation.duration_seconds = payload.duration_seconds
    user_messages = [message for message in conversation.messages if message.role == "user"]
    word_count = sum(len(message.text.split()) for message in user_messages)
    if conversation.mode == "practice":
        attempt = await session.scalar(
            select(PracticeAttempt).where(PracticeAttempt.conversation_id == conversation.id)
        )
        if attempt:
            result = await EvaluationEngine().evaluate(
                [{"role": message.role, "text": message.text} for message in conversation.messages],
                payload.duration_seconds,
            )
            previous = await session.scalar(
                select(PracticeAttempt)
                .where(
                    PracticeAttempt.evaluation_id == attempt.evaluation_id,
                    PracticeAttempt.id != attempt.id,
                )
                .order_by(desc(PracticeAttempt.created_at))
            )
            attempt.transcript = " ".join(message.text for message in user_messages)
            attempt.metrics = result.scores
            attempt.comparison = EvaluationEngine.compare_attempts(
                previous.metrics if previous else {}, result.scores
            )
        conversation.summary = "Coach practice completed."
        conversation.feedback = {"practice": True}
    elif conversation.mode == "ielts":
        conversation.summary = conversation.summary or "IELTS Speaking exam completed."
        conversation.feedback = conversation.feedback or {"exam": {"type": "ielts_speaking"}}
    else:
        conversation.summary = _summary(conversation.topic, len(user_messages), word_count)
        conversation.feedback = {
            "strengths": ["You kept the conversation moving." if user_messages else "Start with a short sentence."],
            "observations": ["You practiced expressing your ideas in English."],
            "mistakes": [],
        }
    stat = conversation.statistics or SessionStat(conversation_id=conversation.id)
    stat.user_turns = len(user_messages)
    stat.assistant_turns = sum(message.role == "assistant" for message in conversation.messages)
    stat.word_count = word_count
    session.add(stat)
    await session.commit()
    await session.refresh(conversation)
    return conversation


def _summary(topic: str, turns: int, words: int) -> str:
    if not turns:
        return f"A short {topic} session."
    return f"You explored {topic} across {turns} user turns and {words} words."
