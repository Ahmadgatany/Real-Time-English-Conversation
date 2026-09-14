from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session
from app.db.models import Conversation, EvaluationRecord, PracticeAttempt, UsageRecord, User
from app.services.personalization.service import build_progress

router = APIRouter(prefix="/api", tags=["progress"])


@router.get("/progress")
async def progress(user: User = Depends(current_user), session: AsyncSession = Depends(db_session)) -> dict:
    evaluations = list(await session.scalars(
        select(EvaluationRecord)
        .join(EvaluationRecord.conversation)
        .where(Conversation.user_id == user.id)
        .order_by(EvaluationRecord.created_at)
    ))
    attempts = list(await session.scalars(
        select(PracticeAttempt)
        .where(PracticeAttempt.user_id == user.id)
        .order_by(PracticeAttempt.created_at)
    ))
    usage = list(await session.scalars(
        select(UsageRecord).where(UsageRecord.user_id == user.id).order_by(desc(UsageRecord.created_at))
    ))
    data = build_progress(
        [{"created_at": item.created_at.isoformat(), "scores": item.scores} for item in evaluations],
        [{"created_at": item.created_at.isoformat(), "metrics": item.metrics} for item in attempts],
    )
    data["cost"] = {
        "total_usd": round(sum(item.cost_usd for item in usage), 6),
        "records": len(usage),
    }
    return data


@router.get("/conversations/{conversation_id}/cost")
async def conversation_cost(
    conversation_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> dict:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    records = list(await session.scalars(
        select(UsageRecord).where(UsageRecord.conversation_id == conversation_id)
    ))
    return {
        "conversation_id": conversation_id,
        "total_usd": round(sum(item.cost_usd for item in records), 6),
        "records": len(records),
    }
