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

engine = create_engine(
    get_settings().database_url,
    # Check a connection still works before using it (e.g. after the
    # database restarted), instead of failing the request.
    pool_pre_ping=True,
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
