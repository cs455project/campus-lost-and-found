# Testing

Tests for TraceBack (IIT Kanpur Lost & Found). The written reports are in `docs/D3/`.

| Level | Where | How to run | Last result (6 Nov 2026) |
|---|---|---|---|
| Unit + integration | `backend/tests/` (21 test files) | `cd backend && pytest` | 459 / 459 passed |
| System | `testing/system_tests.py` | see below | 15 / 15 passed |
| Adversarial | `testing/adversarial_tests.py` | see below | 23 / 23 attacks stopped; `npm audit` and `pip-audit` clean |
| Concurrency | `testing/concurrency_tests.py` | see below | 14 / 14 checks passed |
| Failure / recovery | `testing/failure_recovery_tests.py` | see below | 10 / 10 scenarios recovered |
| Performance / load | `testing/perf/` | see `perf/README.md` | 100 users, ~250 req/s, no errors |

`pytest` runs the unit and integration tests together (459 tests, about 25 s).

## Code coverage

Last measured (6 Nov 2026): **97.9% of lines and 91.2% of branches** with `pytest`
(459 tests). Every backend file is at 91% line coverage or more. Open
`results/coverage_html/index.html` for the line-by-line report.

To measure the unit and integration tests yourself (needs `pip install pytest-cov`):

```bash
cd backend
pytest --cov=app --cov-branch --cov-report=html   # report in backend/htmlcov/index.html
```

## System and adversarial tests

These start the whole app (backend, worker, website) on a **separate, fresh test
database** and drive it through a real browser. They are written for Linux or
macOS (on Windows, use WSL).

One-time setup:

```bash
cd frontend && npm ci && npx next build && cd ..
pip install playwright httpx pillow && playwright install chromium
```

Each run:

```bash
bash testing/start_stack.sh            # fresh test database + backend + worker + website
python testing/system_tests.py         # -> testing/results.json, testing/screenshots/ (copied to results/)
bash testing/start_stack.sh            # fresh again (sign-in codes have a 60 s resend wait)
python testing/adversarial_tests.py    # -> testing/adversarial_results.json
bash testing/start_stack.sh            # fresh again
PSQL="psql -h localhost -U postgres" python testing/concurrency_tests.py   # -> testing/concurrency_results.json (about 5 min)
```

`start_stack.sh` drops and recreates the database named in `TEST_DB`
(default `lostfound_test_system`). Never point it at real data.
Set `PSQL` if your PostgreSQL superuser login differs (default `psql -h localhost -U postgres`).

## Failure and recovery tests

`failure_recovery_tests.py` starts its own copy of the app (fresh database
`lostfound_test_failure`, two API processes, AI queue in the worker, as in
production), then stops the database, kills the API and the worker, cuts off
the AI providers and the mail server, and finally kills everything at once.
It needs to stop and start PostgreSQL, so tell it how:

```bash
pip install aiosmtpd    # a local stand-in for Gmail's SMTP server (fake_smtp.py)
PG_STOP="sudo systemctl stop postgresql" PG_START="sudo systemctl start postgresql" \
PG_KILL="sudo pkill -9 -f postgres" python testing/failure_recovery_tests.py
# -> testing/results/failure_recovery_results.json (about 3 min)
```

Emails run in console mode during these tests: the scripts read sign-in codes
from `testing/run/backend.log`, the same way a person reads their inbox.

`results/` holds the evidence from the run in the report: JSON results,
screenshots, the pytest JUnit file and the coverage data.
