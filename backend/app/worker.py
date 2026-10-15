"""The worker: runs the timed jobs (services/jobs.py) on a schedule.

Run it from the backend folder, in its own terminal, next to uvicorn:

    python -m app.worker              every 5 minutes, until you press Ctrl+C
    python -m app.worker --every 60   every 60 seconds (handy while testing)
    python -m app.worker --once       run every job once, then stop

It also runs the AI queue (services/ai) every few seconds. In development
the API does that by itself (AI_WORKER_IN_API=true), so the worker is only
needed for the timed jobs.

--once is what a server scheduler (e.g. cron, or a cloud "scheduled job")
calls in production (Phase 8). The website works without the worker; only
the reminders, expiry and clean-up wait until it runs.
"""

import argparse
import logging
import time

from app.core.clock import now
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.ai.runner import run_pending as run_ai_jobs
from app.services.jobs import run_all

log = logging.getLogger("worker")


def run_once() -> dict[str, int]:
    # A fresh database session per run, closed afterwards.
    with SessionLocal() as db:
        done = run_all(db, now())
    worked = {name: count for name, count in done.items() if count}
    log.info("Jobs done: %s", worked or "nothing was due")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="run every job once, then stop")
    parser.add_argument("--every", type=int, default=300, help="seconds between runs (default 300)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.once:
        run_once()
        run_ai_jobs()
        return

    log.info("Worker started: timed jobs every %s seconds, AI queue every few seconds. "
             "Press Ctrl+C to stop.", args.every)
    ai_every = get_settings().ai_poll_seconds
    next_timed = 0.0
    try:
        while True:
            if time.monotonic() >= next_timed:
                run_once()
                next_timed = time.monotonic() + args.every
            try:
                run_ai_jobs()
            except Exception:
                log.exception("AI queue round failed")
            time.sleep(ai_every)
    except KeyboardInterrupt:
        log.info("Worker stopped.")


if __name__ == "__main__":
    main()
