"""Fill a TEST database with a year's worth of realistic data for load tests.

Run from backend/ with its virtualenv, on the database start_stack.sh made:
    cd backend && DB_NAME=lostfound_perf python ../testing/perf/seed_load.py

Creates (fast, with plain SQL):
  - 5,000 onboarded students and 5 guards, each with a signed-in session
    whose cookie is "loadtest-<user id>" (written to perf/run/tokens.json)
  - 10,000 items: 6,000 found (about 1,700 held at an office, 300 returned
    in the last 55 days, the rest older and closed) and 4,000 lost
    (1,500 still open)
  - one photo per held found item and per open lost item (hard links to one
    small JPEG, so the disk stays small)
  - 1,500 claims on held items

Never point this at real data: it only adds rows, but they look real.
"""

import json
import os
import random
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))

import psycopg  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_secret  # noqa: E402

random.seed(455)
S = get_settings()
assert "perf" in S.db_name or "test" in S.db_name, "refusing: DB_NAME must be a test database"

N_STUDENTS, N_GUARDS = 5000, 5
N_FOUND, N_FOUND_HELD = 6000, 2000
N_LOST, N_LOST_OPEN = 4000, 1500
N_CLAIMS = 1500

THINGS = [
    ("Casio fx-991EX calculator", 1, "Casio", "black"), ("Black HP laptop charger", 1, "HP", "black"),
    ("boAt earphones in a case", 1, "boAt", "white"), ("Hero cycle with basket", 2, "Hero", "blue"),
    ("Student ID card", 3, None, None), ("Bunch of three keys", 4, None, "silver"),
    ("Brown leather wallet", 5, "Woodland", "brown"), ("Wildcraft backpack", 6, "Wildcraft", "grey"),
    ("Milton steel water bottle", 7, "Milton", "steel"), ("Navy blue hoodie", 8, None, "navy"),
    ("Classmate notebook with notes", 9, "Classmate", None), ("Fastrack wrist watch", 10, "Fastrack", "black"),
    ("Spectacles in a red case", 11, "Lenskart", "red"), ("Cosco badminton racket", 12, "Cosco", "yellow"),
    ("Umbrella, folding", 13, None, "black"), ("Redmi phone with cracked screen", 1, "Redmi", "blue"),
    ("JBL bluetooth speaker", 1, "JBL", "black"), ("Pencil box with drawing tools", 9, "Faber-Castell", "green"),
]
PLACES = ["Lecture Hall Complex", "Central Library", "OAT", "New SAC", "Hall 5 mess", "Hall 12 reading room",
          "CC building", "Pronite ground", "Swimming pool", "Health Centre", "L7", "Tutorial block", "Kendriya Vidyalaya gate"]
WORDS = ("left near the stairs after the class, has a small scratch on the side and a sticker; "
         "please contact if seen, it was in a hurry around evening time").split()


def describe(title):
    return title + ". " + " ".join(random.sample(WORDS, 18))


def main():
    now = datetime.now(timezone.utc)
    conn = psycopg.connect(host=S.db_host, port=S.db_port, dbname=S.db_name, user=S.db_user, password=S.db_password)
    cur = conn.cursor()

    # ---- users and sessions ----
    users = []
    for i in range(N_STUDENTS):
        users.append((f"student{i:05d}@iitk.ac.in", f"Student {i:05d}", f"25{i:06d}", f"98{i:08d}",
                      f"Hall {1 + i % 13}", f"{100 + i % 400}", "STUDENT", None))
    for g in range(N_GUARDS):
        users.append((f"guard{g}.load@gmail.com", f"Guard {g}", None, f"97{g:08d}", None, None, "GUARD", 1 + g))
    with cur.copy("COPY users (email, full_name, roll_number, phone, hall, room, role, office_id) FROM STDIN") as cp:
        for u in users:
            cp.write_row(u)
    cur.execute("SELECT id, role FROM users WHERE email LIKE 'student%%@iitk.ac.in' OR email LIKE 'guard%%.load@gmail.com' ORDER BY id")
    rows = cur.fetchall()
    student_ids = [r[0] for r in rows if r[1] == "STUDENT"]
    guard_ids = [r[0] for r in rows if r[1] == "GUARD"]
    tokens = {}
    with cur.copy("COPY user_sessions (token_hash, user_id, expires_at) FROM STDIN") as cp:
        for uid in student_ids + guard_ids:
            tok = f"loadtest-{uid}-{secrets.token_hex(8)}"
            tokens[uid] = tok
            cp.write_row((hash_secret(tok), uid, now + timedelta(days=30)))

    # ---- items, oldest first, spread over the last year ----
    items = []
    for i in range(N_FOUND):
        title, cat, brand, colour = random.choice(THINGS)
        if i >= N_FOUND - N_FOUND_HELD:
            status = random.choice(["IN_CUSTODY"] * 9 + ["CLAIM_PENDING"])
        else:
            status = random.choice(["RETURNED"] * 6 + ["DISPOSED", "DONATED", "AWAITING_DROPOFF"])
        office = 1 + i % 5
        held = status in ("IN_CUSTODY", "CLAIM_PENDING")
        created = now - timedelta(days=365) + timedelta(days=365 * i / N_FOUND)
        items.append(("FOUND", status, title, describe(title), cat, colour, brand, random.choice(PLACES),
                      min(created.date(), date.today()), random.choice(student_ids), office,
                      office if held else None, created))
    for i in range(N_LOST):
        title, cat, brand, colour = random.choice(THINGS)
        status = "OPEN" if i >= N_LOST - N_LOST_OPEN else random.choice(["RECOVERED", "CLOSED"])
        created = now - timedelta(days=365) + timedelta(days=365 * i / N_LOST)
        items.append(("LOST", status, title, describe(title), cat, colour, brand, random.choice(PLACES),
                      min(created.date(), date.today()), random.choice(student_ids), None, None, created))
    items.sort(key=lambda r: r[-1])
    with cur.copy("COPY items (kind, status, title, description, category_id, colour, brand, location_text, "
                  "happened_on, reporter_id, dropoff_office_id, current_office_id, created_at) FROM STDIN") as cp:
        for it in items:
            cp.write_row(it)

    # Returned records only live 60 days (the worker deletes older ones), so
    # older "returned" items become donated; recent ones get their dates.
    cur.execute("UPDATE items SET status = 'DONATED' WHERE status = 'RETURNED' AND created_at < now() - interval '57 days'")
    cur.execute("UPDATE items SET received_at = created_at + interval '1 day', received_by_id = %s, "
                "returned_at = created_at + interval '3 days' WHERE status = 'RETURNED'", (guard_ids[0],))
    # About 300 items were handed back in the last 55 days.
    cur.execute("UPDATE items SET status = 'RETURNED', current_office_id = NULL, received_by_id = %s, "
                "received_at = created_at, returned_at = now() - random() * interval '55 days' "
                "WHERE id IN (SELECT id FROM items WHERE status = 'IN_CUSTODY' ORDER BY random() LIMIT 300)",
                (guard_ids[0],))

    # ---- photos: one per item people can browse ----
    folder = S.upload_dir / "items"
    folder.mkdir(parents=True, exist_ok=True)
    src = HERE / "phone_photo.jpg"  # 12-megapixel, 2.9 MB: a typical phone photo
    master = folder / "loadtest-master.jpg"
    master.write_bytes(src.read_bytes())
    cur.execute("SELECT id, reporter_id FROM items WHERE status IN ('IN_CUSTODY','CLAIM_PENDING','OPEN')")
    photo_rows = []
    for item_id, rep in cur.fetchall():
        name = f"lt{item_id:06d}{secrets.token_hex(4)}.jpg"
        os.link(master, folder / name)
        photo_rows.append((item_id, name, "image/jpeg", src.stat().st_size, rep))
    with cur.copy("COPY item_photos (item_id, stored_name, content_type, size_bytes, uploaded_by_id) FROM STDIN") as cp:
        for p in photo_rows:
            cp.write_row(p)

    # THUMBS=1: the photos also have their small list copies, as photos
    # uploaded after the optimisation do (they are made at upload time).
    if os.environ.get("THUMBS") == "1" and photo_rows:
        from app.services import storage

        first = storage.make_thumbnail(photo_rows[0][1])
        for p in photo_rows[1:]:
            target = storage.thumbnail_path(p[1])
            if not target.exists():
                os.link(first, target)

    # ---- claims on held items (closed ones, so new claims are still allowed) ----
    cur.execute("SELECT id FROM items WHERE status IN ('IN_CUSTODY','CLAIM_PENDING') ORDER BY id")
    held = [r[0] for r in cur.fetchall()]
    with cur.copy("COPY claims (item_id, claimant_id, status, description, closed_at) FROM STDIN") as cp:
        for _ in range(N_CLAIMS):
            cp.write_row((random.choice(held), random.choice(student_ids), "WITHDRAWN",
                          "It is mine, it has my initials scratched on the back.", now))
    conn.commit()

    cur.execute("ANALYZE")
    out = HERE / "run"
    out.mkdir(exist_ok=True)
    held_found = held
    cur.execute("SELECT id FROM items WHERE status='OPEN'")
    open_lost = [r[0] for r in cur.fetchall()]
    (out / "tokens.json").write_text(json.dumps({
        "students": [tokens[u] for u in student_ids],
        "guards": [tokens[u] for u in guard_ids],
        "held_found_items": held_found,
        "open_lost_items": open_lost,
    }))
    cur.execute("SELECT count(*) FROM users"); nu = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items"); ni = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM item_photos"); npho = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM claims"); nc = cur.fetchone()[0]
    print(f"users={nu} items={ni} photos={npho} claims={nc} sessions={len(tokens)}")


if __name__ == "__main__":
    main()
