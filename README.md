# IITK Lost & Found

A lost-and-found web app for IIT Kanpur. Finders hand items to a campus
security (SIS) office, owners claim them with proof, and a guard verifies
the claim before the owner collects the item in person.

Feature set and visual design inspired by [FindIt](https://github.com/agankit2004/FindIt);
all code in this repository was written independently.

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Alembic |
| Database | PostgreSQL |
| Frontend | Next.js (React) |

## Project structure

```
lost-found-iitk/
├── backend/
│   ├── app/
│   │   ├── main.py          creates the FastAPI app, registers routers
│   │   ├── seed.py          inserts starting categories and offices
│   │   ├── cli.py           command-line tools (make-admin, demo-items)
│   │   ├── worker.py        runs the timed jobs every few minutes
│   │   ├── core/            settings, database, security, permission checks, errors
│   │   ├── models/          database tables as Python classes
│   │   ├── schemas/         shapes of API requests and responses (validation)
│   │   ├── services/        business rules (sign-in, profiles, guards, items, custody, claims, returned items, timed jobs, matching, photos, email)
│   │   └── routers/         API endpoints, one file per area
│   ├── docs/MATCHING.md     how to plug in AI matching (Similar items found)
│   ├── tests/               automated tests (run with: pytest)
│   ├── uploads/             item photos and claim proof images (not in Git)
│   ├── alembic/             database migrations
│   ├── alembic.ini          Alembic settings
│   ├── requirements.txt     Python packages
│   └── .env.example         template for your local settings
└── frontend/                Next.js website (see frontend/README.md)
```

## First-time setup (Windows, PyCharm terminal)

Run these from the project folder, `lost-found-iitk`.

1. **Install the Python packages** (including test tools) into the project's `.venv`:
   ```
   pip install -r backend/requirements-dev.txt
   ```

2. **Create your settings file.** Copy `backend/.env.example` to
   `backend/.env`, then open `.env` and set:
   - `DB_PASSWORD`: the password of the `lostfound` PostgreSQL user
   - `SECRET_KEY`: a long random string, generated with
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   ```
   copy backend\.env.example backend\.env
   ```

3. **Create the tables** (runs every migration that hasn't run yet):
   ```
   cd backend
   alembic upgrade head
   ```

4. **Add starting data** (categories and offices; safe to run again):
   ```
   python -m app.seed
   ```

5. **Start the API:**
   ```
   uvicorn app.main:app --reload
   ```
   - Health check: http://127.0.0.1:8000/health
   - Interactive API docs: http://127.0.0.1:8000/docs

6. **Make yourself the admin** (only possible from the command line):
   ```
   python -m app.cli make-admin your-id@iitk.ac.in
   ```

## Running the website

With the API running, open a **second terminal**:
```
cd frontend
npm install
npm run dev
```
Then open http://localhost:3000 and sign in. In development, the sign-in
code is printed in the terminal running the API.

## The worker (timed jobs)

In a **third terminal**, from `backend/`:
```
python -m app.worker
```
Every 5 minutes it does whatever has become due:

| Job | When |
|---|---|
| Remind a finder to hand the item in | 24 hours after a found report, if not handed in |
| Remind a claimant to collect | 3 days after approval, if not collected |
| Remind guards about waiting claims | a claim has waited 3 days for review |
| Expire an approval | 7 days after approval, if not collected |
| Delete proof images | 2 days after a claim closes |
| Delete a returned item completely | 60 days after it was returned |

Each reminder is sent only once. The website works without the worker; the
jobs simply wait until it runs. Use `--every 60` to run every minute while testing.

## Signing in through the API docs (alternative)

1. In `/docs`, call `POST /auth/request-code` with your @iitk.ac.in email.
2. The code is **printed in the terminal running uvicorn** (no email
   account needed while `EMAIL_BACKEND=console`).
3. Call `POST /auth/verify-code` with the email and code. The browser now
   has a session cookie, so the other endpoints in `/docs` work as you.
4. Call `POST /users/me/onboarding` to fill in your profile.

## Everyday commands (from `backend/`)

| Task | Command |
|---|---|
| Start the API | `uvicorn app.main:app --reload` |
| Run the automated tests | `pytest` |
| Make the first admin (later admins are added on Manage staff) | `python -m app.cli make-admin email@gmail.com` |
| Add sample items (to try filters and pages) | `python -m app.cli demo-items --count 30` |
| Run the timed jobs (reminders, expiry, clean-up) | `python -m app.worker` |
| Run the timed jobs once and stop | `python -m app.worker --once` |
| Apply new migrations | `alembic upgrade head` |
| Make a migration after changing a model | `alembic revision --autogenerate -m "what changed"` |
| Undo the last migration | `alembic downgrade -1` |
| See the SQL a migration would run | `alembic upgrade head --sql` |
| Check models and database match | `alembic check` |

Always open and read an autogenerated migration before running it.

## Staff features (guards and admin)

- **Staff desk** (`/staff`): found items waiting for drop-off, held at offices, or closed;
  filter by office; search by words or by reference (e.g. `FND-0031`).
- **Receive** an item when the finder hands it in: confirm the office and note its condition.
- **Register walk-in item**: for items handed in without an online report.
- **Move** an item between offices, and **dispose of or donate** unclaimed items.
- **Custody history** on every found item: who received or moved it, where, and when.
- **Claims desk** (`/staff/claims`): claims to review, office visits, items ready to collect, closed.
  On a claim: compare the claimant's description with the item itself, then approve, reject
  (reason required) or ask the claimant to visit the office. Hand over only after checking the
  student ID card; the return time comes from the server clock.
- **Manage staff** (`/admin/staff`, admins only): add guards or other admins (by @iitk.ac.in or
  @gmail.com email), change someone's role (guard ↔ admin) or usual office, remove them.
  You can't change your own role or remove yourself, so there's always at least one admin.

## Claims (students)

- On a found item, **This is mine** opens the claim form: a description only the owner would
  know (required) and up to 3 proof images (optional, private to the claimant and staff).
- **My claims** (`/my-claims`) shows each claim and what to do next. Approved claimants
  collect the item in person within 7 days with their student ID card.
- Several people can claim the same item. While one claim is approved, the others wait;
  when the item is handed over, the others close automatically.

## Returned items

`/returned` lists found items handed back to their owners, newest first, with a
category filter. Everyone sees what was returned, from which office and when;
staff also see who collected it, the claim, the guard and notes. Records are
deleted 60 days after the return.

## Similar items found (AI matching)

On a lost report, the reporter (and staff) see found items that may be it; on a
found item, lost reports that may be its owner's. The app has the section, the
endpoint `GET /items/{id}/similar` and the safety rules, but **no matching logic**:
`MATCHING_BACKEND=placeholder` suggests nothing, and the page says matching isn't
switched on yet. To plug in a real matcher, see
[backend/docs/MATCHING.md](backend/docs/MATCHING.md).

## Roles

| Role | Can do |
|---|---|
| Student | report lost/found items, browse, claim items |
| Guard | everything a student can, plus receive, move and hand over items, and review claims, at every office |
| Admin | everything a guard can, plus add, change and remove guards and other admins |
