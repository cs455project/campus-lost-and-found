"""Shared test setup.

Each test runs inside a database transaction that is rolled back (undone)
at the end. So tests can create users and codes freely, your real data is
never changed, and every test starts from the same clean state.

Run all tests from the backend folder with:  pytest
"""

import os

# Before the app is imported: tests run AI jobs themselves, so no background
# AI runner, and no model download (the small built-in embedder is used).
os.environ["AI_WORKER_IN_API"] = "false"
os.environ["EMBEDDING_BACKEND"] = "hashing"
# Never call real AI providers from tests (even if .env has keys): agent
# tests plug in a fake LLM instead.
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_FALLBACK_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Item, LoginCode, User, UserRole, UserSession  # noqa: E402
from app.services import email as email_service  # noqa: E402


@pytest.fixture(autouse=True)
def temp_uploads(tmp_path, monkeypatch):
    """Every test saves photos and proofs in its own temporary folder, so
    tests never leave files in your real backend/uploads folder."""
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    return tmp_path / "uploads"


@pytest.fixture
def db():
    connection = engine.connect()
    outer = connection.begin()
    # create_savepoint: when app code calls commit(), only a "savepoint"
    # inside our outer transaction is committed, so the rollback below
    # still undoes everything.
    session = Session(bind=connection, join_transaction_mode="create_savepoint",
                      expire_on_commit=False)
    # Start every test from empty user tables, so your own development data
    # (accounts, recent login codes) can never affect a test. This happens
    # inside the transaction, so the rollback below brings your data back.
    session.execute(delete(Item))  # photos go too (ON DELETE CASCADE)
    session.execute(delete(UserSession))
    session.execute(delete(LoginCode))
    session.execute(delete(User))
    yield session
    session.close()
    outer.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def email_log(monkeypatch, tmp_path):
    """Emails tests don't intercept are logged here instead of the real
    email_logs table, and their HTML previews go to a temporary folder."""
    logged: list[tuple] = []
    monkeypatch.setattr(email_service, "_record", lambda to, mail, status, error=None: logged.append(
        (to, mail.kind, mail.ref, status, error)))
    monkeypatch.setattr(email_service, "PREVIEW_DIR", tmp_path / "emails")
    return logged


@pytest.fixture
def sent_codes(monkeypatch):
    """Instead of printing or emailing codes, collect them here so tests
    can read them: sent_codes["someone@iitk.ac.in"] -> "123456"."""
    codes: dict[str, str] = {}
    monkeypatch.setattr(
        email_service, "send_login_code",
        lambda to, code, minutes_valid: codes.__setitem__(to, code),
    )
    return codes


@pytest.fixture
def make_client(db):
    """Make a fresh browser-like client. Each client keeps its own cookies,
    so two clients act as two different people."""
    app.dependency_overrides[get_db] = lambda: db
    clients = []

    def _make() -> TestClient:
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()
    app.dependency_overrides.clear()


@pytest.fixture
def sign_in(make_client, sent_codes):
    """Return a client signed in as `email` (requesting and entering the code)."""

    def _sign_in(email: str) -> TestClient:
        client = make_client()
        assert client.post("/auth/request-code", json={"email": email}).status_code == 202
        response = client.post(
            "/auth/verify-code", json={"email": email, "code": sent_codes[email]}
        )
        assert response.status_code == 200, response.text
        return client

    return _sign_in


@pytest.fixture
def make_user(db):
    """Create a user directly in the database (skipping sign-in)."""

    def _make(email: str, role: UserRole = UserRole.STUDENT, **fields) -> User:
        user = User(email=email, role=role, **fields)
        db.add(user)
        db.commit()
        return user

    return _make
