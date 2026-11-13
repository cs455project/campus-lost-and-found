"""Tests for the API's plumbing and a few edge cases of its endpoints: the
health check, database sessions, start-up and shut-down, signing out without
a session, private proof images, and tidying of report fields."""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.core import database
from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import ClaimProof, Office
from app.services import storage
from app.services.ai import runner
from tests.test_claims import (  # noqa: F401  (fixtures)
    PNG,
    claim,
    held_item,
    person,
    place,
)

# ---------------- health check ----------------


def test_health_reports_ok_when_the_database_answers(make_client):
    response = make_client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_503_when_the_database_is_unreachable():
    class UnreachableDatabase:
        def execute(self, statement):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_db] = lambda: UnreachableDatabase()
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}


# ---------------- database sessions ----------------


class TrackedSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_each_request_gets_a_session_that_is_closed_afterwards(monkeypatch):
    sessions = []
    monkeypatch.setattr(database, "SessionLocal", lambda: sessions.append(TrackedSession()) or sessions[-1])
    dependency = get_db()
    session = next(dependency)
    assert session is sessions[0] and not session.closed
    with pytest.raises(StopIteration):
        next(dependency)  # the request finished
    assert session.closed


def test_the_session_is_closed_even_when_the_endpoint_fails(monkeypatch):
    session = TrackedSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    dependency = get_db()
    next(dependency)
    with pytest.raises(ValueError):
        dependency.throw(ValueError("the endpoint crashed"))
    assert session.closed


def test_the_pool_has_a_connection_for_every_request_in_flight():
    pool = database.engine.pool
    assert pool.size() + pool._max_overflow >= get_settings().api_max_in_flight


# ---------------- start-up and shut-down ----------------


class FakeRunner:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_the_api_starts_the_ai_runner_and_stops_it_on_shutdown(monkeypatch):
    background = FakeRunner()
    monkeypatch.setattr(runner, "start_in_api", lambda: background)
    with TestClient(app) as client:  # runs the start-up code
        assert client.get("/health").status_code in (200, 503)
        assert not background.stopped
    assert background.stopped  # and the shut-down code


def test_the_api_starts_fine_without_an_ai_runner(monkeypatch):
    monkeypatch.setattr(runner, "start_in_api", lambda: None)
    with TestClient(app) as client:
        assert client.get("/health").status_code in (200, 503)


# ---------------- signing out ----------------


def test_signing_out_without_a_session_is_harmless(make_client):
    response = make_client().post("/auth/logout")
    assert response.status_code == 204
    assert "lf_session=" in response.headers.get("set-cookie", "")  # the cookie is cleared anyway


# ---------------- private proof images ----------------


@pytest.fixture
def two_claims_with_proofs(person, held_item):  # noqa: F811
    owner = person()
    first = claim(owner, held_item["id"]).json()["id"]
    upload = owner.post(f"/claims/{first}/proofs", files={"file": ("p.png", io.BytesIO(PNG), "image/png")})
    proof = upload.json()["proof_ids"][0]
    other_owner = person()
    second = claim(other_owner, held_item["id"]).json()["id"]
    return {"owner": owner, "first": first, "proof": proof, "other_owner": other_owner, "second": second,
            "guard": held_item["guard"]}


def test_a_proof_can_only_be_opened_through_its_own_claim(two_claims_with_proofs):
    c = two_claims_with_proofs
    assert c["guard"].get(f"/claims/{c['first']}/proofs/{c['proof']}").status_code == 200
    # Guards see both claims, but the proof belongs to the first one only.
    response = c["guard"].get(f"/claims/{c['second']}/proofs/{c['proof']}")
    assert response.status_code == 404 and "No proof image" in response.json()["detail"]
    assert c["guard"].get(f"/claims/{c['first']}/proofs/999999").status_code == 404


def test_a_proof_whose_file_was_deleted_is_reported_missing(db, two_claims_with_proofs):
    c = two_claims_with_proofs
    proof = db.get(ClaimProof, c["proof"])
    storage.path_for(proof.stored_name, folder="proofs").unlink()  # e.g. removed by the clean-up job
    response = c["owner"].get(f"/claims/{c['first']}/proofs/{c['proof']}")
    assert response.status_code == 404 and "missing" in response.json()["detail"]


# ---------------- tidying report fields ----------------


def test_optional_fields_left_empty_or_blank_are_stored_as_not_given(person, place):  # noqa: F811
    reporter = person()
    body = {"title": "  Black   backpack ", "description": "  Wildcraft.\nName inside.  ",
            "category_id": place["bags"], "location_text": "Library", "happened_on": "2026-11-01",
            "colour": None, "brand": "   "}
    response = reporter.post("/items/lost", json=body)
    assert response.status_code == 201, response.text
    item = reporter.get(f"/items/{response.json()['id']}").json()
    assert item["title"] == "Black backpack"  # extra spaces squeezed
    assert item["description"] == "Wildcraft.\nName inside."  # line break kept, ends trimmed
    assert item["colour"] is None and item["brand"] is None


def test_a_title_of_only_spaces_is_refused(person, place):  # noqa: F811
    body = {"title": "    ", "description": "x", "category_id": place["bags"], "location_text": "Library",
            "happened_on": "2026-11-01"}
    assert person().post("/items/lost", json=body).status_code == 422


# ---------------- offices list (for the drop-off and office pickers) ----------------


def test_the_office_list_shows_open_offices_main_office_first(db, person, make_client):  # noqa: F811
    db.add(Office(name="AAA Closed Office", location_description="Old block", opening_hours="never",
                  is_active=False))
    db.execute(update(Office).where(Office.name == "Security Sub-office B").values(is_active=False))
    db.commit()
    offices = person().get("/offices").json()
    names = [o["name"] for o in offices]
    assert names[0] == "Main Security Office" and offices[0]["is_main"] is True
    assert names[1:] == sorted(names[1:])  # the others in name order
    assert "AAA Closed Office" not in names and "Security Sub-office B" not in names
    assert set(offices[0]) == {"id", "name", "location_description", "opening_hours", "contact_phone", "is_main"}
    assert make_client().get("/offices").status_code == 401  # signed-in users only
