"""Runs the AI queue in the background, every few seconds.

Started by the API on start-up when AI_WORKER_IN_API=true (the default),
so in development there's nothing extra to run. `python -m app.worker`
runs the same loop; running both at once is safe (see queue.py).
"""

import logging
import threading

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.ai import queue
from app.services.ai import semantic  # noqa: F401  (registers the job handlers)
from app.services.ai.embeddings import get_embedder

log = logging.getLogger(__name__)


def run_pending() -> int:
    """Run every waiting AI job once, in a fresh database session."""
    with SessionLocal() as db:
        return queue.run_all(db)


class BackgroundRunner:
    def __init__(self, every_seconds: float):
        self.every = every_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="ai-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _loop(self) -> None:
        # Load the embedding model now (first run may download it), not
        # when the first report arrives.
        try:
            get_embedder()
        except Exception:
            log.exception("Embedder failed to load")
        while not self._stop.is_set():
            try:
                done = run_pending()
                if done:
                    log.info("AI runner: %s job(s) done", done)
            except Exception:
                # e.g. the database restarted: try again next round
                log.exception("AI runner round failed")
            self._stop.wait(self.every)


def start_in_api() -> BackgroundRunner | None:
    settings = get_settings()
    if not settings.ai_worker_in_api:
        return None
    runner = BackgroundRunner(settings.ai_poll_seconds)
    runner.start()
    log.info("AI runner started inside the API (every %ss)", settings.ai_poll_seconds)
    return runner
