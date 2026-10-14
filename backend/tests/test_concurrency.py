"""Real concurrency tests (FR-CON-01..04, NFR-CON-01/03/04, US-12, US-14, US-16).

Unlike the other tests, these use several database connections at once,
each in its own thread, and really COMMIT. That's the only way to check row
locks: a single rolled-back test transaction can't race with itself.

Each test creates its own users, office, category and items with unique
names and deletes them again at the end, so your development data is left
alone.

A Barrier makes every thread start its action at the same instant, to make
the race as likely as possible. Whatever the order, the database must end up
in exactly one valid state.
"""

import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.core.errors import AppError
from app.models import (
    Category,
    Claim,
    ClaimEvent,
    ClaimStatus,
    CustodyEvent,
    CustodyEventType,
    Item,
    ItemKind,
    ItemStatus,
    Office,
    User,
    UserRole,
)
from app.schemas.staff import DisposeIn, ReceiveIn
from app.services import claims as claims_service
from app.services import custody as custody_service
from app.services import email as email_service


@pytest.fixture(autouse=True)
def no_email(monkeypatch):
    monkeypatch.setattr(email_service, "send_email", lambda *args, **kwargs: None)


@pytest.fixture
def world():
    """Committed test data, removed afterwards."""
    tag = uuid.uuid4().hex[:8]
    created = {"users": [], "items": []}
    with SessionLocal() as db:
        office = Office(name=f"Race Office {tag}", location_description="Gate", opening_hours="24 hours")
        category = Category(name=f"Race Cat {tag}", slug=f"race-{tag}", sort_order=999)
        db.add_all([office, category])
        db.commit()
        ids = {"office": office.id, "category": category.id, "tag": tag}

    def user(role=UserRole.STUDENT) -> int:
        with SessionLocal() as db:
            n = len(created["users"]) + 1
            extra = {"roll_number": f"R{tag}{n}"} if role == UserRole.STUDENT else {}
            person = User(email=f"race{tag}{n}@iitk.ac.in", role=role, full_name=f"Racer {n}", **extra)
            db.add(person)
            db.commit()
            created["users"].append(person.id)
            return person.id

    def item(finder_id: int, status=ItemStatus.IN_CUSTODY) -> int:
        with SessionLocal() as db:
            held = status in (ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING)
            thing = Item(kind=ItemKind.FOUND, status=status, title="Race bag", description="Blue bag",
                         category_id=ids["category"], location_text="Library", happened_on=date.today(),
                         reporter_id=finder_id, dropoff_office_id=ids["office"],
                         current_office_id=ids["office"] if held else None)
            db.add(thing)
            db.commit()
            created["items"].append(thing.id)
            return thing.id

    ids.update(user=user, item=item)
    yield ids

    with SessionLocal() as db:
        # Items first: claims, claim events and custody events go with them (CASCADE).
        db.execute(delete(Item).where(Item.id.in_(created["items"])))
        db.execute(delete(User).where(User.id.in_(created["users"])))
        db.execute(delete(Category).where(Category.id == ids["category"]))
        db.execute(delete(Office).where(Office.id == ids["office"]))
        db.commit()


def race(*actions):
    """Run each action in its own thread and session, all starting together.
    Returns one result per action: "ok", or the HTTP status of the AppError."""
    barrier = threading.Barrier(len(actions))
    results = [None] * len(actions)

    def run(index, action):
        with SessionLocal() as db:
            barrier.wait()
            try:
                action(db)
                results[index] = "ok"
            except AppError as error:
                db.rollback()
                results[index] = error.status_code

    threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(actions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "a thread is stuck (deadlock?)"
    return results


def _submit(db, item_id, user_id):
    return claims_service.submit(db, db.get(Item, item_id), db.get(User, user_id), "It is mine")


def _claim_under_review(world, item_id, guard_id) -> int:
    with SessionLocal() as db:
        claim = _submit(db, item_id, world["user"]())
        claims_service.start_review(db, claim, db.get(User, guard_id))
        return claim.id


@pytest.mark.parametrize("attempt", range(3))
def test_two_guards_approving_two_claims_on_one_item(world, attempt):
    """US-12 / FR-CON-01: at most one claim per item can be approved."""
    guard_a, guard_b = world["user"](UserRole.GUARD), world["user"](UserRole.GUARD)
    item_id = world["item"](world["user"]())
    claim_a = _claim_under_review(world, item_id, guard_a)
    claim_b = _claim_under_review(world, item_id, guard_a)

    results = race(
        lambda db: claims_service.approve(db, db.get(Claim, claim_a), db.get(User, guard_a), None),
        lambda db: claims_service.approve(db, db.get(Claim, claim_b), db.get(User, guard_b), None),
    )
    assert sorted(results, key=str) == [409, "ok"]
    with SessionLocal() as db:
        approved = db.scalar(select(func.count()).select_from(Claim)
                             .where(Claim.item_id == item_id, Claim.status == ClaimStatus.APPROVED))
        assert approved == 1


def test_two_guards_receiving_the_same_item(world):
    """US-14 / FR-CUS-04: receipt is recorded exactly once."""
    guard_a, guard_b = world["user"](UserRole.GUARD), world["user"](UserRole.GUARD)
    item_id = world["item"](world["user"](), status=ItemStatus.AWAITING_DROPOFF)

    def receive(guard_id):
        return lambda db: custody_service.receive_item(
            db, db.get(Item, item_id), db.get(User, guard_id), ReceiveIn(office_id=world["office"]))

    results = race(receive(guard_a), receive(guard_b))
    assert sorted(results, key=str) == [403, "ok"]
    with SessionLocal() as db:
        assert db.get(Item, item_id).status == ItemStatus.IN_CUSTODY
        received = db.scalar(select(func.count()).select_from(CustodyEvent).where(
            CustodyEvent.item_id == item_id, CustodyEvent.event_type == CustodyEventType.RECEIVED))
        assert received == 1


def test_double_hand_over_returns_item_once(world):
    """US-16 / FR-CLM-10: one hand-over, one RETURNED record."""
    guard_a, guard_b = world["user"](UserRole.GUARD), world["user"](UserRole.GUARD)
    item_id = world["item"](world["user"]())
    claim_id = _claim_under_review(world, item_id, guard_a)
    with SessionLocal() as db:
        claims_service.approve(db, db.get(Claim, claim_id), db.get(User, guard_a), None)

    def hand_over(guard_id):
        return lambda db: claims_service.hand_over(db, db.get(Claim, claim_id), db.get(User, guard_id), None)

    results = race(hand_over(guard_a), hand_over(guard_b))
    assert sorted(results, key=str) == [403, "ok"]
    with SessionLocal() as db:
        assert db.get(Item, item_id).status == ItemStatus.RETURNED
        returned = db.scalar(select(func.count()).select_from(CustodyEvent).where(
            CustodyEvent.item_id == item_id, CustodyEvent.event_type == CustodyEventType.RETURNED))
        assert returned == 1


def test_approve_and_reject_at_once_leave_a_consistent_history(world):
    """US-15: whatever order the database chooses, the claim's status equals
    its last history entry, and every entry starts where the previous ended."""
    guard_a, guard_b = world["user"](UserRole.GUARD), world["user"](UserRole.GUARD)
    item_id = world["item"](world["user"]())
    claim_id = _claim_under_review(world, item_id, guard_a)

    race(
        lambda db: claims_service.approve(db, db.get(Claim, claim_id), db.get(User, guard_a), None),
        lambda db: claims_service.reject(db, db.get(Claim, claim_id), db.get(User, guard_b), "No match"),
    )
    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        events = list(db.scalars(select(ClaimEvent).where(ClaimEvent.claim_id == claim_id).order_by(ClaimEvent.id)))
        assert claim.status == events[-1].to_status
        for before, after in zip(events, events[1:]):
            assert after.from_status == before.to_status


def test_claim_and_disposal_at_once(world):
    """A claim and a disposal racing: never a claim on a disposed item."""
    guard = world["user"](UserRole.GUARD)
    item_id = world["item"](world["user"]())
    claimant = world["user"]()

    results = race(
        lambda db: _submit(db, item_id, claimant),
        lambda db: custody_service.dispose(db, db.get(Item, item_id), db.get(User, guard),
                                           DisposeIn(outcome="DONATED", note="Unclaimed")),
    )
    assert results.count("ok") == 1
    with SessionLocal() as db:
        item = db.get(Item, item_id)
        claims = db.scalar(select(func.count()).select_from(Claim).where(Claim.item_id == item_id))
        if item.status == ItemStatus.DONATED:
            assert claims == 0
        else:
            assert item.status == ItemStatus.CLAIM_PENDING and claims == 1


def test_same_person_submitting_twice_at_once(world):
    """FR-CLM-04 with the unique index as second line: one open claim per person."""
    item_id = world["item"](world["user"]())
    claimant = world["user"]()
    results = race(lambda db: _submit(db, item_id, claimant), lambda db: _submit(db, item_id, claimant))
    assert sorted(results, key=str) == [409, "ok"]
