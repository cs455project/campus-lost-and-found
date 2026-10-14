"""Row locks for state changes (FR-CON-02/03, NFR-CON-03, ADR-003).

Every action that changes an item's custody state, or any claim on an item,
first locks that item's row with SELECT ... FOR UPDATE and re-reads it.

Why: two guards (or a guard and the finder) can press buttons at the same
moment. Without a lock both requests read the same old status, both pass
their checks, and both write. With the lock, the second request waits until
the first commits, then re-reads the NEW status and its checks fail cleanly
(403/409) instead of corrupting the item or its history.

Lock order is always: the item row first, then its claims. Using one order
everywhere means two requests can never wait for each other (deadlock).
The lock is released by the commit (or rollback) that ends the request.
"""

from sqlalchemy.orm import Session

from app.models import Claim, Item


def lock_item(db: Session, item: Item) -> Item:
    """Lock the item row and reload it, so checks see the committed state.
    Call this BEFORE changing anything on the item (refresh discards
    unsaved changes)."""
    db.refresh(item, with_for_update=True)
    return item


def lock_claim(db: Session, claim: Claim) -> Claim:
    """Lock the claim's item (all claims on one item are serialised by it),
    then reload the claim itself."""
    db.refresh(claim.item, with_for_update=True)
    db.refresh(claim)
    return claim
