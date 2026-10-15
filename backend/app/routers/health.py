"""GET /health: a quick check that the API is running and can reach
the database. Useful while developing, and for the server you deploy to,
which can call it to check the app is alive."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        # The smallest possible query: proves the connection works.
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # 503 = "Service Unavailable": the API runs, but can't do its job.
        return JSONResponse(
            status_code=503, content={"status": "error", "database": "unreachable"}
        )
    return {"status": "ok", "database": "ok"}
