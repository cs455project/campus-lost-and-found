"""The boundary between this app and the AI matching (built by a teammate).

    THIS FILE DOES NOT DECIDE WHICH ITEMS ARE SIMILAR.

It defines the "socket" a matcher plugs into, plus a placeholder matcher
that finds nothing. The real matcher (for example pgvector embeddings) is
written separately and switched on with one setting:

    MATCHING_BACKEND=placeholder     (default: matching is off)
    MATCHING_BACKEND=<your name>     (after registering it in MATCHERS below)

How it fits together:

    item page -> GET /items/{id}/similar -> similar_items()  (this file: rules)
                                               -> matcher.find_similar()  (teammate: ranking)

The matcher only RANKS. Everything about safety stays here, so a matcher
can never show something it shouldn't:
  - only the opposite kind (a lost item gets found items, and the reverse)
  - only items on the public pages (never an item waiting for drop-off,
    returned, or closed), and never the item itself
  - unknown ids and duplicates are dropped, scores are clamped to 0..1
  - at most `limit` results, best first
  - if the matcher crashes, the page still works and shows no matches

See backend/docs/MATCHING.md for the full contract.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import Item, ItemKind, ItemStatus, User
from app.models.item import PUBLIC_STATUSES

log = logging.getLogger(__name__)

MAX_RESULTS = 6


# ---------------- The contract ----------------


@dataclass(frozen=True)
class Match:
    """One suggestion from a matcher."""

    item_id: int
    score: float  # 0.0 (unrelated) to 1.0 (certainly the same item)
    reason: str | None = None  # optional short text, e.g. "same category and colour"
    method: str | None = None  # how it was ranked: "ai" (the agent) or "basic" (rules)


class Matcher(Protocol):
    """What every matcher must provide. A Protocol is a "shape": any class
    with these attributes and methods fits, without inheriting from anything."""

    # False only for the placeholder, so the page can say "matching is off".
    enabled: bool

    def find_similar(self, db: Session, item: Item, limit: int) -> list[Match]:
        """Return up to `limit` candidate items of the OPPOSITE kind, best first.
        Must not change the database."""
        ...

    def item_saved(self, db: Session, item: Item) -> None:
        """Called after an item is created or changed (e.g. a photo was added),
        so the matcher can (re)compute what it needs, like an embedding."""
        ...

    def item_removed(self, item_id: int) -> None:
        """Called before an item is deleted for good (Phase 7 purge)."""
        ...


class PlaceholderMatcher:
    """Matching switched off: never suggests anything, stores nothing."""

    enabled = False

    def find_similar(self, db: Session, item: Item, limit: int) -> list[Match]:
        return []

    def item_saved(self, db: Session, item: Item) -> None:
        pass

    def item_removed(self, item_id: int) -> None:
        pass


# Name (from the MATCHING_BACKEND setting) -> matcher class.
# The teammate adds one line here, e.g.  "pgvector": PgVectorMatcher,
MATCHERS: dict[str, type] = {
    "placeholder": PlaceholderMatcher,
}

_active: Matcher | None = None


def _register_builtin() -> None:
    """Our own semantic matcher (services/ai/semantic.py). Imported here, not
    at the top, because that module imports this one (for Match)."""
    if "semantic" not in MATCHERS:
        from app.services.ai.semantic import SemanticMatcher

        MATCHERS["semantic"] = SemanticMatcher


def get_matcher() -> Matcher:
    """The matcher chosen in settings, created once and reused."""
    global _active
    if _active is None:
        _register_builtin()
        name = get_settings().matching_backend
        if name not in MATCHERS:
            raise ValueError(f"Unknown MATCHING_BACKEND '{name}'. Known: {', '.join(MATCHERS)}")
        _active = MATCHERS[name]()
    return _active


def set_matcher(matcher: Matcher | None) -> None:
    """Swap the matcher (used by tests). None = go back to the setting."""
    global _active
    _active = matcher


# ---------------- Rules around the matcher (not matching logic) ----------------

# Statuses where suggestions are useful: a lost item still missing, or a
# found item that hasn't gone back to anyone yet.
_LOOKING = {
    ItemKind.LOST: [ItemStatus.OPEN],
    ItemKind.FOUND: [ItemStatus.AWAITING_DROPOFF, ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING],
}


def can_see_similar(item: Item, user: User) -> bool:
    """Suggestions are for the person who reported the item, and for staff."""
    return item.status in _LOOKING[item.kind] and (item.reporter_id == user.id or user.is_staff)


def similar_items(db: Session, item: Item, limit: int = MAX_RESULTS) -> dict:
    """Ask the matcher, then keep only suggestions that are safe to show."""
    matcher = get_matcher()
    try:
        raw = matcher.find_similar(db, item, limit)
    except Exception:
        # A broken matcher must never break the item page.
        log.exception("Matcher failed for item %s", item.id)
        raw = []

    # Best score first; keep the first suggestion for each id.
    best: dict[int, Match] = {}
    for match in sorted(raw, key=lambda m: m.score, reverse=True):
        if match.item_id != item.id and match.item_id not in best:
            best[match.item_id] = match

    wanted_kind = ItemKind.FOUND if item.kind == ItemKind.LOST else ItemKind.LOST
    allowed = {
        candidate.id: candidate
        for candidate in db.scalars(
            select(Item)
            .where(Item.id.in_(best), Item.kind == wanted_kind, Item.status.in_(PUBLIC_STATUSES[wanted_kind]))
            .options(selectinload(Item.photos), selectinload(Item.category), selectinload(Item.current_office))
        )
    }

    results = []
    for item_id, match in best.items():
        if item_id in allowed:
            results.append({"item": allowed[item_id], "score": min(max(match.score, 0.0), 1.0),
                            "reason": match.reason, "method": match.method})
        if len(results) == limit:
            break
    # "pending" while the AI is still working on this item, so the page can
    # say "looking for matches..." and check again shortly.
    status = None
    if hasattr(matcher, "status"):
        try:
            status = matcher.status(db, item)
        except Exception:
            log.exception("Matcher status failed for item %s", item.id)
    return {"enabled": matcher.enabled, "status": status, "matches": results}


def notify_saved(db: Session, item: Item) -> None:
    """Tell the matcher an item was created or changed. Never fails the
    caller: a report is saved even if the matcher is down."""
    try:
        get_matcher().item_saved(db, item)
    except Exception:
        log.exception("Matcher could not process item %s", item.id)


def notify_removed(item_id: int) -> None:
    try:
        get_matcher().item_removed(item_id)
    except Exception:
        log.exception("Matcher could not forget item %s", item_id)
