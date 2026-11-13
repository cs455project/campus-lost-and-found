"""Limit how many requests the API works on at once.

Why: each request may hold one database connection, and the pool has a
fixed number of them (core/database.py). If more requests run than there
are connections, some wait for a connection while holding one of the 40
worker threads; with enough of them, every thread is waiting and the
requests that hold the connections can't get a thread to finish. The API
then stops answering (found by the load test, Nov 2026).

So at most `api_max_in_flight` requests run at a time, and the database pool
is sized to match. Extra requests wait here, before they hold anything,
and get "server busy" (503) only if they wait longer than api_queue_seconds.
"""

import asyncio
import json

from app.core.config import get_settings

# Cheap requests that never touch the database skip the queue.
SKIP = {"/health"}


class ConcurrencyLimit:
    """Pure ASGI middleware (it wraps the app, it doesn't buffer responses)."""

    def __init__(self, app):
        self.app = app
        settings = get_settings()
        self.slots = asyncio.Semaphore(settings.api_max_in_flight)
        self.wait = settings.api_queue_seconds

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in SKIP:
            await self.app(scope, receive, send)
            return
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=self.wait)
        except TimeoutError:
            await _busy(send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.slots.release()


async def _busy(send):
    body = json.dumps({"detail": "The server is busy. Please try again in a moment."}).encode()
    await send({"type": "http.response.start", "status": 503,
                "headers": [(b"content-type", b"application/json"), (b"retry-after", b"5"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
