from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import current_user, db_session
from app.api.schemas import EvaluationOutput, PracticeCreate, PracticeOutput
from app.db.models import Conversation, EvaluationRecord, PracticeAttempt, User
from app.services.evaluation.engine import EvaluationEngine

router = APIRouter(prefix="/api", tags=["evaluation"])


async def owned_evaluation(evaluation_id: str, user: User, session: AsyncSession) -> EvaluationRecord:
    evaluation = await session.scalar(
        select(EvaluationRecord)
        .join(EvaluationRecord.conversation)
        .where(EvaluationRecord.id == evaluation_id, Conversation.user_id == user.id)
        .options(selectinload(EvaluationRecord.practice_attempts))
    )
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return evaluation


async def evaluate_exam(
    conversation: Conversation,
    session: AsyncSession,
    request: Request,
) -> EvaluationRecord:
    existing = await session.scalar(
        select(EvaluationRecord).where(EvaluationRecord.conversation_id == conversation.id)
    )
    if existing:
        return existing
    engine = EvaluationEngine(request.app.state.ai_provider)
    result = await engine.evaluate(
        [{"role": message.role, "text": message.text} for message in conversation.messages],
        conversation.duration_seconds,
        conversation.feedback.get("audio_metrics") if conversation.feedback else None,
    )
    evaluation = EvaluationRecord(
        conversation_id=conversation.id,
        scores=result.as_dict()["scores"],
        metrics=result.metrics,
        feedback=result.feedback,
        recommendations=result.recommendations,
        coach_exercises=result.coach_exercises,
    )
    session.add(evaluation)
    await session.commit()
    await session.refresh(evaluation)
    return evaluation


@router.get("/conversations/{conversation_id}/evaluation", response_model=EvaluationOutput)
async def get_evaluation(
    conversation_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> EvaluationRecord:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .options(selectinload(Conversation.messages))
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.mode != "ielts" or conversation.ended_at is None:
        raise HTTPException(status_code=409, detail="Evaluation is available after a completed IELTS exam")
    return await evaluate_exam(conversation, session, request)


@router.post("/evaluations/{evaluation_id}/practice", response_model=PracticeOutput, status_code=status.HTTP_201_CREATED)
async def create_practice(
    evaluation_id: str,
    payload: PracticeCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> PracticeAttempt:
    evaluation = await owned_evaluation(evaluation_id, user, session)
    conversation = Conversation(user_id=user.id, topic="Coach practice", mode="practice")
    session.add(conversation)
    await session.flush()
    attempt = PracticeAttempt(
        evaluation_id=evaluation.id,
        conversation_id=conversation.id,
        user_id=user.id,
        skill=payload.skill,
        prompt=payload.prompt,
        target_duration_seconds=payload.target_duration_seconds,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return attempt


@router.get("/practice/{attempt_id}", response_model=PracticeOutput)
async def get_practice(
    attempt_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> PracticeAttempt:
    attempt = await session.scalar(
        select(PracticeAttempt).where(PracticeAttempt.id == attempt_id, PracticeAttempt.user_id == user.id)
    )
    if not attempt:
        raise HTTPException(status_code=404, detail="Practice attempt not found")
    return attempt
