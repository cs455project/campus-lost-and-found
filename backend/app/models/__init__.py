"""Importing every model here registers its table on Base.metadata.
Alembic imports this package to discover all tables, so every new
model file must be added to this list."""

from app.models.auth import LoginCode, UserSession
from app.models.base import Base
from app.models.category import Category
from app.models.claim import (
    OPEN_CLAIM_STATUSES,
    Claim,
    ClaimDetail,
    ClaimEvent,
    ClaimProof,
    ClaimStatus,
    DetailSource,
    DetailVerdict,
)
from app.models.custody import CustodyEvent, CustodyEventType
from app.models.item import Item, ItemKind, ItemPhoto, ItemStatus
from app.models.office import Office
from app.models.user import User, UserRole

__all__ = [
    "OPEN_CLAIM_STATUSES",
    "Base",
    "Category",
    "Claim",
    "ClaimDetail",
    "ClaimEvent",
    "ClaimProof",
    "ClaimStatus",
    "CustodyEvent",
    "CustodyEventType",
    "DetailSource",
    "DetailVerdict",
    "Item",
    "ItemKind",
    "ItemPhoto",
    "ItemStatus",
    "LoginCode",
    "Office",
    "User",
    "UserRole",
    "UserSession",
]
