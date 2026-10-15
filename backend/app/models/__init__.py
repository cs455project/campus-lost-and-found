"""Importing every model here registers its table on Base.metadata.
Alembic imports this package to discover all tables, so every new
model file must be added to this list."""

from app.models.ai import (
    AgentRun,
    AiJob,
    MatchNotification,
    MatchSuggestion,
    OwnerAlert,
)
from app.models.auth import LoginCode, UserSession
from app.models.base import Base
from app.models.category import Category
from app.models.claim import (
    OPEN_CLAIM_STATUSES,
    Claim,
    ClaimEvent,
    ClaimProof,
    ClaimStatus,
)
from app.models.custody import CustodyEvent, CustodyEventType
from app.models.email_log import EmailLog
from app.models.item import Item, ItemKind, ItemPhoto, ItemStatus
from app.models.office import Office
from app.models.user import User, UserRole

__all__ = [
    "OPEN_CLAIM_STATUSES",
    "AgentRun",
    "AiJob",
    "Base",
    "Category",
    "Claim",
    "ClaimEvent",
    "ClaimProof",
    "ClaimStatus",
    "CustodyEvent",
    "CustodyEventType",
    "EmailLog",
    "Item",
    "ItemKind",
    "ItemPhoto",
    "ItemStatus",
    "LoginCode",
    "MatchNotification",
    "MatchSuggestion",
    "Office",
    "OwnerAlert",
    "User",
    "UserRole",
    "UserSession",
]
