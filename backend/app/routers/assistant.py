"""The sidebar help assistant (services/ai/assistant.py)."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.deps import DbSession, OnboardedUser
from app.services.ai import assistant

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    # The browser keeps the conversation and sends it with each question.
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ChatOut(BaseModel):
    reply: str
    provider: str | None


@router.post("/chat", response_model=ChatOut)
def chat(data: ChatIn, user: OnboardedUser, db: DbSession):
    """Answer the last question. Read-only: the assistant can look up found
    items, the asker's own reports and claims, and the offices."""
    return assistant.answer(db, user, [m.model_dump() for m in data.messages])
