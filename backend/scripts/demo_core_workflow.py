"""Demonstration of the core transactional workflow (Deliverable 2).

Runs the whole found-item -> custody -> claim -> score -> approval ->
hand-over flow through the REAL API code and the REAL PostgreSQL database,
and prints each step. Nothing is mocked except the email: sign-in codes are
captured instead of printed.

    cd backend
    python -m scripts.demo_core_workflow            # leaves the demo data, so you can open it in the website
    python -m scripts.demo_core_workflow --cleanup  # deletes everything it created at the end

Prerequisites: `alembic upgrade head` and `python -m app.seed` have been run.

Steps (requirement IDs from the D1 SRS):
  1. Finder reports a found bicycle            -> AWAITING_DROPOFF          FR-REP-02, FR-CUS-01/02/08
  2. Guard receives it at an office            -> IN_CUSTODY                FR-CUS-03/04/05
  3. Two students claim it, with identifying details                        FR-CLM-01/02/04
  4. Score C = 50M + 20R + 30H for each (staff only)                        FR-SCORE-01
  5. Guard checks details: M changes, status does not                       FR-CLM-07, FR-SCORE-04
  6. Two guards approve the two claims AT THE SAME MOMENT -> one wins (409) FR-CON-01..04
  7. Hand-over after ID check -> RETURNED, the other claim closes           FR-CUS-07, FR-CLM-10
  8. The returned item can't be claimed again                               FR-CLM-10
"""

import argparse
import threading
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.clock import today
from app.core.database import SessionLocal
from app.main import app
from app.models import Category, Item, Office, User, UserRole
from app.services import email as email_service

codes: dict[str, str] = {}


def step(number: int, text: str) -> None:
    print(f"\n[{number}] {text}")


def show(label: str, value) -> None:
    print(f"    {label:<34} {value}")


def ok(response, expected: int = 200):
    if response.status_code != expected:
        raise SystemExit(f"    UNEXPECTED {response.status_code}: {response.text}")
    return response.json()


def sign_in(email: str) -> TestClient:
    client = TestClient(app)
    ok(client.post("/auth/request-code", json={"email": email}), 202)
    ok(client.post("/auth/verify-code", json={"email": email, "code": codes[email]}))
    return client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="delete the demo data at the end")
    args = parser.parse_args()

    # Capture codes and silence other emails (approval notices etc.).
    email_service.send_login_code = lambda to, code, minutes_valid: codes.__setitem__(to, code)
    email_service.send_email = lambda *a, **k: None

    tag = uuid.uuid4().hex[:6]
    people = {
        "finder": (f"demo.{tag}.finder@iitk.ac.in", UserRole.STUDENT, "Asha Finder", f"F{tag}"),
        "owner": (f"demo.{tag}.owner@iitk.ac.in", UserRole.STUDENT, "Ravi Owner", f"O{tag}"),
        "rival": (f"demo.{tag}.rival@iitk.ac.in", UserRole.STUDENT, "Kiran Rival", f"R{tag}"),
        "guard1": (f"demo.{tag}.guard1@gmail.com", UserRole.GUARD, "Guard Mehta", None),
        "guard2": (f"demo.{tag}.guard2@gmail.com", UserRole.GUARD, "Guard Rao", None),
    }
    with SessionLocal() as db:
        cycle = db.scalar(select(Category).where(Category.slug == "cycle"))
        office = db.scalar(select(Office).where(Office.is_active).order_by(Office.is_main.desc()))
        if cycle is None or office is None:
            raise SystemExit("Run `python -m app.seed` first.")
        for email, role, name, roll in people.values():
            db.add(User(email=email, role=role, full_name=name, roll_number=roll))
        db.commit()
        cycle_id, office_id, office_name = cycle.id, office.id, office.name

    clients = {key: sign_in(value[0]) for key, value in people.items()}
    finder, owner, rival = clients["finder"], clients["owner"], clients["rival"]
    guard1, guard2 = clients["guard1"], clients["guard2"]
    item_id = None

    try:
        step(1, "Finder reports a found bicycle (custody-required category)")
        item = ok(finder.post("/items/found", json={
            "title": f"Hero cycle (demo {tag})", "description": "Black Hero Sprint with a front basket.",
            "category_id": cycle_id, "location_text": "Hall 5 cycle stand", "happened_on": str(today()),
            "dropoff_office_id": office_id}), 201)
        item_id = item["id"]
        show("item", item["ref"])
        show("status", item["status"])

        step(2, f"Guard Mehta receives it at {office_name}")
        item = ok(guard1.post(f"/staff/items/{item_id}/receive",
                              json={"office_id": office_id, "condition_note": "Rear tyre flat"}))
        show("status", item["status"])
        show("custody history", [e["event_type"] for e in item["custody_events"]])
        show("2nd receive attempt", guard2.post(f"/staff/items/{item_id}/receive",
                                                 json={"office_id": office_id}).status_code)

        step(3, "Two students claim it, each listing identifying details")
        claim_owner = ok(owner.post(f"/items/{item_id}/claims", json={
            "description": "It's my cycle, bought in July.",
            "details": ["Red bell on the left handle", "Sticker 'IITK Cycling Club' on the frame",
                        "Lock key number 4417"]}), 201)
        claim_rival = ok(rival.post(f"/items/{item_id}/claims", json={
            "description": "I lost a black cycle near Hall 5.",
            "details": ["Blue bell", "No sticker"]}), 201)
        show("owner's claim", f"{claim_owner['ref']} {claim_owner['status']}")
        show("rival's claim", f"{claim_rival['ref']} {claim_rival['status']}")
        show("claimant sees a score?", claim_owner["score"])
        show("rival can open owner's claim?", rival.get(f"/claims/{claim_owner['id']}").status_code)

        step(4, "Staff see C = 50M + 20R + 30H for each claim")
        for claim in (claim_owner, claim_rival):
            score = ok(guard1.get(f"/claims/{claim['id']}"))["score"]
            show(claim["ref"], f"C={score['score']}  (M={score['m']}, R={score['r']}, H={score['h']})")

        step(5, "Guard reviews and checks each detail against the cycle")
        for claim, verdicts in ((claim_owner, ["MATCH", "MATCH", "MATCH"]), (claim_rival, ["NO_MATCH", "NO_MATCH"])):
            ok(guard1.post(f"/staff/claims/{claim['id']}/start-review"))
            for detail, verdict in zip(claim["details"], verdicts):
                result = ok(guard1.put(f"/staff/claims/{claim['id']}/details/{detail['id']}",
                                       json={"verdict": verdict}))
            show(claim["ref"], f"C={result['score']['score']}  status={result['status']} (score never decides)")
        desk = ok(guard1.get("/staff/claims", params={"stage": "review", "sort": "score"}))
        show("desk order by score", [f"{row['ref']}={row['score']}" for row in desk["items"]
                                     if row["item"]["id"] == item_id])

        step(6, "Two guards press Approve on the two claims at the same moment")
        barrier = threading.Barrier(2)
        results = {}

        def approve(name, client, claim):
            barrier.wait()
            results[name] = client.post(f"/staff/claims/{claim['id']}/approve", json={"note": "Checked"})

        threads = [threading.Thread(target=approve, args=("Mehta -> " + claim_owner["ref"], guard1, claim_owner)),
                   threading.Thread(target=approve, args=("Rao -> " + claim_rival["ref"], guard2, claim_rival))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for name, response in results.items():
            show(name, f"{response.status_code} {response.json().get('status') or response.json().get('detail')}")
        winner = next(r.json() for r in results.values() if r.status_code == 200)
        assert sum(r.status_code == 200 for r in results.values()) == 1, "both approvals succeeded!"
        show("approved claims on the item", 1)
        if winner["id"] != claim_owner["id"]:
            # The race went to the weaker claim. The score never decides, but a
            # human can correct: reject it, then approve the right one.
            ok(guard1.post(f"/staff/claims/{winner['id']}/reject",
                           json={"note": "Details didn't match the cycle; approved by mistake"}))
            winner = ok(guard1.post(f"/staff/claims/{claim_owner['id']}/approve", json={"note": "All details match"}))
            show("guard corrects", f"{claim_rival['ref']} REJECTED, {claim_owner['ref']} {winner['status']}")

        step(7, "Approved claimant at the desk: guard checks the ID card and hands over")
        handed = ok(guard1.post(f"/staff/claims/{winner['id']}/hand-over",
                                json={"id_checked": True, "note": "ID card matched"}))
        show("claim", f"{handed['ref']} {handed['status']}")
        item = ok(guard1.get(f"/items/{item_id}"))
        show("item status", item["status"])
        show("custody history", [e["event_type"] for e in item["custody_events"]])
        others = {c["ref"]: c["status"] for c in item["claims"]}
        show("all claims on the item", others)
        show("second hand-over attempt", guard2.post(f"/staff/claims/{winner['id']}/hand-over",
                                                      json={"id_checked": True}).status_code)

        step(8, "Nobody can claim a returned item")
        show("new claim on returned item", rival.post(f"/items/{item_id}/claims",
                                                     json={"description": "mine"}).status_code)
        print("\nDemo finished: every step behaved as required.")
        if not args.cleanup:
            print(f"Demo data kept. Sign in as {people['guard1'][0]} to see it in the website.")
    finally:
        for client in clients.values():
            client.close()
        if args.cleanup:
            with SessionLocal() as db:
                if item_id:
                    db.execute(delete(Item).where(Item.id == item_id))
                db.execute(delete(User).where(User.email.in_([p[0] for p in people.values()])))
                db.commit()
            print("Demo data deleted.")


if __name__ == "__main__":
    main()
