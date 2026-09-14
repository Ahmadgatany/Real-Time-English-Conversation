from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=100)


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class PreferencesInput(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    voice: str = Field(default="Kore", max_length=60)
    speaking_style: str = Field(default="natural", max_length=40)
    auto_start: bool = True


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    display_name: str
    preferences: dict[str, Any]
    created_at: datetime


class ConversationCreate(BaseModel):
    topic: str = Field(default="Free Conversation", min_length=1, max_length=120)
    mode: Literal["general", "ielts"] = "general"


class ConversationOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    topic: str
    mode: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int
    summary: str | None
    feedback: dict[str, Any]


class MessageOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    role: str
    text: str
    created_at: datetime


class ConversationDetail(ConversationOutput):
    messages: list[MessageOutput]


class EndConversationInput(BaseModel):
    duration_seconds: int = Field(ge=0, le=86400)


class TopicOutput(BaseModel):
    id: str
    title: str
    description: str
    prompt: str


class EvaluationOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str
    scores: dict[str, Any]
    metrics: dict[str, Any]
    feedback: dict[str, Any]
    recommendations: list[str]
    coach_exercises: list[dict[str, Any]]
    is_ai_estimate: bool
    created_at: datetime


class PracticeCreate(BaseModel):
    skill: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=1, max_length=500)
    target_duration_seconds: int = Field(default=60, ge=15, le=600)


class PracticeOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str
    evaluation_id: str
    skill: str
    prompt: str
    target_duration_seconds: int
    transcript: str
    metrics: dict[str, Any]
    comparison: dict[str, Any]
    created_at: datetime
