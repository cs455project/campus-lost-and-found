"""US-22 / SCRUM-51: the deterministic verification score C = 50M + 20R + 30H.

FR-SCORE-01  the formula is computed exactly and reproducibly       (unit tests)
FR-SCORE-02  it does not depend on the AI matching agent              (import checks)
FR-SCORE-03  the agent cannot calculate or change the weights         (import checks, constants)
FR-SCORE-04  the score only orders review; it never decides anything  (API tests)
"""

import ast
import inspect
import itertools
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete

from app import cli
from app.core.clock import today
from app.core.database import SessionLocal
from app.models import Category, Claim, DetailVerdict, Item, ItemKind, ItemStatus, Office, User, UserRole
from app.services import email as email_service
from app.services import matching
from app.services import verification_score as vs

MATCH, NO = DetailVerdict.MATCH, DetailVerdict.NO_MATCH


# ---------------- The formula (unit tests) ----------------


def test_weights_are_fixed_at_50_20_30():
    assert (vs.WEIGHT_MATCH, vs.WEIGHT_RECENCY, vs.WEIGHT_HISTORY) == (50, 20, 30)
    assert vs.WEIGHTS == {"M": 50, "R": 20, "H": 30}


@pytest.mark.parametrize(("m", "r", "h", "expected"), [
    (0, 0, 0, 0.0),
    (1, 1, 1, 100.0),
    (1, 0, 0, 50.0),
    (0, 1, 0, 20.0),
    (0, 0, 1, 30.0),
    (0.5, 0.5, 0.5, 50.0),
    (0.6667, 1, 0.3333, 63.33),
])
def test_compute(m, r, h, expected):
    assert vs.compute(m, r, h) == expected


@pytest.mark.parametrize("bad", [(-0.1, 0, 0), (0, 1.01, 0), (0, 0, 2)])
def test_compute_rejects_factors_outside_0_to_1(bad):
    with pytest.raises(ValueError):
        vs.compute(*bad)


def test_match_fraction_counts_unchecked_as_not_matched():
    assert vs.match_fraction([]) == 0.0
    assert vs.match_fraction([None, None]) == 0.0
    assert vs.match_fraction([MATCH, None]) == 0.5
    assert vs.match_fraction([MATCH, NO, MATCH]) == 0.6667
    assert vs.match_fraction([MATCH, MATCH]) == 1.0


@pytest.mark.parametrize(("days", "expected"), [(0, 1.0), (3, 0.9), (15, 0.5), (30, 0.0), (90, 0.0), (-2, 1.0)])
def test_recency_factor(days, expected):
    found = date(2026, 10, 1)
    assert vs.recency_factor(found, found + timedelta(days=days)) == expected


@pytest.mark.parametrize(("returns", "expected"), [(0, 0.0), (1, 0.3333), (2, 0.6667), (3, 1.0), (50, 1.0), (-1, 0.0)])
def test_history_factor(returns, expected):
    assert vs.history_factor(returns) == expected


def test_score_is_reproducible_from_stored_factors():
    verdict_sets = [list(v) for n in range(4) for v in itertools.product([MATCH, NO, None], repeat=n)]
    for verdicts in verdict_sets:
        for days, returns in itertools.product([0, 7, 29], [0, 1, 5]):
            r = vs.recency_factor(date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=days))
            result = vs.breakdown(verdicts, r, vs.history_factor(returns))
            assert 0 <= result.score <= 100
            assert vs.compute(result.m, result.r, result.h) == result.score


# ---------------- Independence from the AI matcher ----------------


def _imports(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_score_does_not_use_the_ai_matcher():
    assert not any("matching" in name for name in _imports(vs))


def test_ai_matcher_cannot_reach_the_score_or_claims():
    names = _imports(matching)
    assert not any("verification_score" in n or "claims" in n for n in names)
    source = inspect.getsource(matching)
    for forbidden in ("score_new_claim", "rescore", "WEIGHT_", "Claim(", "approve(", "hand_over("):
        assert forbidden not in source


# ---------------- Through the API ----------------


@pytest.fixture(autouse=True)
def no_email(monkeypatch):
    monkeypatch.setattr(email_service, "send_email", lambda *args, **kwargs: None)


@pytest.fixture
def setup(db, make_user, sign_in):
    bags = Category(name="Score Bags", slug="score-bags", sort_order=1)
    office = Office(name="Score Office", location_description="Gate", opening_hours="24 hours")
    db.add_all([bags, office])
    db.commit()
    count = {"n": 0}

    def person(role=UserRole.STUDENT, **fields):
        count["n"] += 1
        email = f"scoreperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"S{count['n']:04d}"} if role == UserRole.STUDENT else {}
        user = make_user(email, role=role, full_name=f"Score Person {count['n']}", **extra, **fields)
        return sign_in(email), user

    guard, _ = person(UserRole.GUARD)

    def held_item(days_ago=0, finder=None):
        finder_client = finder or person()[0]
        body = {"title": "Grey bag", "description": "Grey bag.", "category_id": bags.id, "location_text": "OAT",
                "happened_on": str(today() - timedelta(days=days_ago)), "dropoff_office_id": office.id}
        item_id = finder_client.post("/items/found", json=body).json()["id"]
        assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": office.id}).status_code == 200
        return item_id

    return {"person": person, "guard": guard, "held_item": held_item, "office": office.id, "bags": bags.id}


def send(client, item_id, details=("Cat sticker", "Rs 200 inside", "Broken zip")):
    response = client.post(f"/items/{item_id}/claims", json={"description": "Mine", "details": list(details)})
    assert response.status_code == 201, response.text
    return response.json()


def test_new_claim_score_is_staff_only(setup):
    owner, _ = setup["person"]()
    body = send(owner, setup["held_item"]())
    assert body["score"] is None  # the claimant never sees it

    staff_view = setup["guard"].get(f"/claims/{body['id']}").json()["score"]
    # Found today (R = 1), no history (H = 0), nothing checked yet (M = 0).
    assert staff_view["m"] == 0 and staff_view["r"] == 1 and staff_view["h"] == 0
    assert staff_view["score"] == 20.0
    assert staff_view["weights"] == {"M": 50, "R": 20, "H": 30}
    assert (staff_view["matched"], staff_view["checked"], staff_view["total"]) == (0, 0, 3)


def test_recency_uses_the_found_date(setup):
    owner, _ = setup["person"]()
    body = send(owner, setup["held_item"](days_ago=15))
    assert setup["guard"].get(f"/claims/{body['id']}").json()["score"]["r"] == 0.5


def test_guard_checks_raise_m_but_never_change_the_status(setup):
    guard = setup["guard"]
    owner, _ = setup["person"]()
    body = send(owner, setup["held_item"]())
    guard.post(f"/staff/claims/{body['id']}/start-review")
    ids = [d["id"] for d in body["details"]]

    for detail_id in ids:
        result = guard.put(f"/staff/claims/{body['id']}/details/{detail_id}", json={"verdict": "MATCH"}).json()
    assert result["score"]["m"] == 1.0 and result["score"]["score"] == 70.0
    # A perfect match is still only advice: a guard must still decide.
    assert result["status"] == "UNDER_REVIEW"
    assert result["can_approve"] is True

    result = guard.put(f"/staff/claims/{body['id']}/details/{ids[0]}", json={"verdict": "NO_MATCH"}).json()
    assert result["score"]["m"] == 0.6667
    assert result["score"]["score"] == vs.compute(0.6667, 1.0, 0.0)
    assert result["status"] == "UNDER_REVIEW"


def test_low_score_can_still_be_approved_by_a_human(setup):
    guard = setup["guard"]
    owner, _ = setup["person"]()
    body = send(owner, setup["held_item"](days_ago=60), details=[])
    assert guard.get(f"/claims/{body['id']}").json()["score"]["score"] == 0.0
    guard.post(f"/staff/claims/{body['id']}/start-review")
    assert guard.post(f"/staff/claims/{body['id']}/approve", json={}).json()["status"] == "APPROVED"


def test_hand_over_counts_a_verified_return_for_the_finder(setup, db):
    guard = setup["guard"]
    finder_client, finder = setup["person"]()
    owner, _ = setup["person"]()
    body = send(owner, setup["held_item"](finder=finder_client))
    guard.post(f"/staff/claims/{body['id']}/start-review")
    guard.post(f"/staff/claims/{body['id']}/approve", json={})
    assert guard.post(f"/staff/claims/{body['id']}/hand-over", json={"id_checked": True}).status_code == 200
    db.refresh(finder)
    assert finder.verified_returns == 1

    # When that finder later claims something themselves, H reflects it.
    later = send(finder_client, setup["held_item"]())
    assert guard.get(f"/claims/{later['id']}").json()["score"]["h"] == 0.3333


def test_walk_in_returns_are_not_counted_for_the_guard(setup, db):
    guard = setup["guard"]
    walk_in = guard.post("/staff/items/walk-in", json={
        "title": "Umbrella", "description": "Black umbrella", "category_id": setup["bags"], "location_text": "Gate",
        "happened_on": str(today()), "office_id": setup["office"]}).json()
    owner, _ = setup["person"]()
    body = send(owner, walk_in["id"], details=[])
    guard.post(f"/staff/claims/{body['id']}/start-review")
    guard.post(f"/staff/claims/{body['id']}/approve", json={})
    guard.post(f"/staff/claims/{body['id']}/hand-over", json={"id_checked": True})
    staff_user = db.query(User).filter(User.role == UserRole.GUARD).first()
    db.refresh(staff_user)
    assert staff_user.verified_returns == 0


def test_desk_sorts_by_score_and_hides_it_from_claimants(setup):
    guard = setup["guard"]
    item_id = setup["held_item"]()
    low_client, _ = setup["person"]()
    high_client, _ = setup["person"](verified_returns=3)
    low = send(low_client, item_id)
    high = send(high_client, item_id)

    oldest = guard.get("/staff/claims", params={"stage": "review"}).json()["items"]
    assert [row["id"] for row in oldest][:2] == [low["id"], high["id"]]
    by_score = guard.get("/staff/claims", params={"stage": "review", "sort": "score"}).json()["items"]
    assert [row["id"] for row in by_score][:2] == [high["id"], low["id"]]
    assert by_score[0]["score"] == 50.0  # R = 1, H = 1, M = 0

    mine = high_client.get("/claims/mine").json()["items"]
    assert mine[0]["score"] is None
    assert guard.get("/staff/claims", params={"sort": "best"}).status_code == 422


def test_students_cannot_open_the_claims_desk(setup):
    student, _ = setup["person"]()
    assert student.get("/staff/claims", params={"sort": "score"}).status_code == 403


# ---------------- rescore-claims command ----------------


def test_rescore_claims_scores_old_claims_only():
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        office = Office(name=f"Rescore {tag}", location_description="x", opening_hours="x")
        category = Category(name=f"Rescore {tag}", slug=f"rescore-{tag}", sort_order=999)
        finder = User(email=f"rf{tag}@iitk.ac.in", full_name="F", roll_number=f"RF{tag}")
        claimant = User(email=f"rc{tag}@iitk.ac.in", full_name="C", roll_number=f"RC{tag}", verified_returns=3)
        db.add_all([office, category, finder, claimant])
        db.flush()
        item = Item(kind=ItemKind.FOUND, status=ItemStatus.CLAIM_PENDING, title="Old", description="Old",
                    category_id=category.id, location_text="x", happened_on=today(), reporter_id=finder.id,
                    dropoff_office_id=office.id, current_office_id=office.id)
        db.add(item)
        db.flush()
        old = Claim(item_id=item.id, claimant_id=claimant.id, description="pre-score claim")
        db.add(old)
        db.commit()
        ids = {"item": item.id, "users": [finder.id, claimant.id], "office": office.id,
               "category": category.id, "claim": old.id}
    try:
        assert cli.rescore_claims(only_ids=[ids["claim"]]) == 1
        with SessionLocal() as db:
            claim = db.get(Claim, ids["claim"])
            assert (claim.score_m, claim.score_r, claim.score_h, claim.score) == (0.0, 1.0, 1.0, 50.0)
        assert cli.rescore_claims(only_ids=[ids["claim"]]) == 0  # already scored: nothing to do
    finally:
        with SessionLocal() as db:
            db.execute(delete(Item).where(Item.id == ids["item"]))
            db.execute(delete(User).where(User.id.in_(ids["users"])))
            db.execute(delete(Category).where(Category.id == ids["category"]))
            db.execute(delete(Office).where(Office.id == ids["office"]))
            db.commit()
