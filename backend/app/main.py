"""Entry point: creates the FastAPI app and plugs in each group of endpoints.

Run it from the backend folder with:
    uvicorn app.main:app --reload
("app.main" = this file, ":app" = the variable below, --reload = restart on code changes)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import AppError
from app.routers import admin, auth, claims, health, items, reference, staff, users

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
    version="0.5.0",
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


@app.exception_handler(AppError)
def handle_app_error(request: Request, error: AppError) -> JSONResponse:
    """Turn any AppError raised by a service into {"detail": "..."}."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.message})


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(reference.router)
app.include_router(items.router)
app.include_router(staff.router)
app.include_router(claims.router)
app.include_router(admin.router)
