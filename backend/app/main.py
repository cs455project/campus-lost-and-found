"""Entry point: creates the FastAPI app and plugs in each group of endpoints.

Run it from the backend folder with:
    uvicorn app.main:app --reload
("app.main" = this file, ":app" = the variable below, --reload = restart on code changes)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core.capacity import ConcurrencyLimit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.headers import SecurityHeaders
from app.routers import admin, assistant, auth, claims, health, items, reference, staff, users

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the API starts, and again when it stops."""
    from app.services.ai.runner import start_in_api

    runner = start_in_api()  # the AI queue runs in the background (docs/AI.md)
    yield
    if runner:
        runner.stop()


app = FastAPI(
    lifespan=lifespan,
    title="TraceBack API (IIT Kanpur Lost & Found)",
    description="Report, find, claim and return lost items through campus security offices.",
    version="0.6.0",
    # The interactive API docs list every endpoint. Handy in development;
    # switched off on the server (API_DOCS=false in deploy/docker-compose.yml).
    docs_url="/docs" if settings.api_docs else None,
    redoc_url="/redoc" if settings.api_docs else None,
    openapi_url="/openapi.json" if settings.api_docs else None,
)

# CORS: browsers block a page on one address (the Next.js site, port 3000)
# from calling an API on another (this API, port 8000) unless the API
# explicitly allows it. We allow only our own frontend.
# allow_credentials lets the browser send the session cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last, so it runs first: a request waits here before it holds a
# thread or a database connection (see core/capacity.py).
app.add_middleware(ConcurrencyLimit)
# Outermost, so even a "server busy" answer carries the security headers.
app.add_middleware(SecurityHeaders)


@app.exception_handler(AppError)
def handle_app_error(request: Request, error: AppError) -> JSONResponse:
    """Turn any AppError raised by a service into {"detail": "..."}."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.message})


@app.exception_handler(OperationalError)
def handle_database_down(request: Request, error: OperationalError) -> JSONResponse:
    """The database can't be reached (restarting, or down). Answer with a
    clear 503 at once, instead of a bare 500 and a dropped connection; the
    connection pool reconnects by itself when the database is back (failure
    test FR-01). Other database errors are not hidden by this."""
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": "TraceBack is briefly unavailable. Please try again in a moment."})


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(reference.router)
app.include_router(items.router)
app.include_router(staff.router)
app.include_router(claims.router)
app.include_router(admin.router)
app.include_router(assistant.router)
