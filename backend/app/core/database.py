"""Database connection.

- engine:       manages a pool of open connections to PostgreSQL.
- SessionLocal: makes a "session", a short conversation with the database.
                Changes inside a session are saved together with commit().
- get_db:       gives each API request its own session and always closes it.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    # Check a connection still works before using it (e.g. after the
    # database restarted), instead of failing the request.
    pool_pre_ping=True,
    # Enough connections for every request the API works on at once
    # (api_max_in_flight, enforced in main.py), plus a few for the AI runner
    # and start-up. 10 stay open; the others open when needed and close
    # when the rush is over. Without this, a busy API could deadlock: every
    # worker thread waiting for a connection, while the requests holding the
    # connections waited for a free thread (load test, Nov 2026).
    pool_size=10,
    max_overflow=max(0, _settings.api_max_in_flight + 5 - 10),
    pool_timeout=10,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: endpoints receive a session by declaring
    `db: Session = Depends(get_db)`. The `finally` block closes it even
    if the endpoint raises an error, so connections are never leaked."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
