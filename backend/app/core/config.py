"""App settings, read from environment variables or the backend/.env file.

Keeping settings here (instead of hard-coding them in the code) means:
- passwords never appear in the source code or on GitHub,
- each team member and the deployed server can use different values
  without changing any code.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# backend/  (this file is backend/app/core/config.py, so go up three levels)
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Absolute path, so it works no matter which folder you run commands from.
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Database ----
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "lostfound"
    db_user: str = "lostfound"
    db_password: str  # no default: the app refuses to start without it

    # ---- Security ----
    # min_length: refuse to start with a short, guessable secret.
    secret_key: str = Field(min_length=32)
    session_days: int = 30
    cookie_secure: bool = False
    session_cookie_name: str = "lf_session"

    # Login codes
    login_code_minutes: int = 10  # a code expires after this long
    login_code_max_attempts: int = 5  # wrong guesses allowed per code
    login_code_resend_seconds: int = 60  # wait between code requests
    login_code_max_per_hour: int = 5  # codes one email can request per hour

    # ---- App rules ----
    allowed_email_domain: str = "iitk.ac.in"
    # Extra domains that security staff (guards, admin) may use, comma-separated.
    # A staff address works only once the admin has made it a guard.
    staff_email_domains: str = "gmail.com"
    frontend_origin: str = "http://localhost:3000"
    # Dates like "found on" are in campus time, not the server's own zone.
    timezone: str = "Asia/Kolkata"

    # ---- Item photos ----
    upload_dir: Path = BACKEND_DIR / "uploads"
    max_photos_per_item: int = 3
    max_photo_mb: int = 5

    # ---- Email ----
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    # The portal's name, used in emails and page titles.
    portal_name: str = "TraceBack"
    portal_tagline: str = "IIT Kanpur Lost & Found"

    # ---- Similar items (AI matching, see docs/AI.md and docs/MATCHING.md) ----
    # "semantic" = our AI matching (embeddings + top-K). "placeholder" = off.
    matching_backend: str = "semantic"
    # How many suggestions each report shows, and the lowest score shown.
    match_top_k: int = 5
    match_min_score: float = 0.30

    # ---- AI agent ----
    # Master switch: false = no calls to any AI API (local matching still works).
    ai_enabled: bool = True
    # Process the AI queue inside the API process (handy in development:
    # no extra terminal). On a server you can turn this off and run
    # `python -m app.worker` instead. Both together are safe too.
    ai_worker_in_api: bool = True
    ai_poll_seconds: float = 3.0
    # Embeddings: "local" = fastembed ONNX model on this machine (downloaded
    # once, ~70 MB). "hashing" = tiny built-in fallback, no download.
    embedding_backend: Literal["local", "hashing"] = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: Path = BACKEND_DIR / ".cache" / "models"

    # Agent's language model (Phase B): Groq first, Gemini as the fallback.
    # Both speak the same OpenAI-style API. Keys only in .env, never in code.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""  # your Groq key
    llm_model: str = "openai/gpt-oss-20b"
    llm_fallback_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_fallback_api_key: str = ""  # empty = use GEMINI_API_KEY
    llm_fallback_model: str = ""  # empty = use GEMINI_MODEL
    # Google Gemini (free tier): photo descriptions (Phase C) and the
    # fallback brain above. Pick a free Flash model shown in AI Studio.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # When the AI agent ranks matches (the maths always runs first):
    #   off        never: rule-based matching only (no LLM calls)
    #   ambiguous  only when the maths isn't sure (close scores, or a top
    #              score in the uncertain range). Saves most LLM calls.
    #   always     every report (e.g. for a demo)
    agent_matching: Literal["off", "ambiguous", "always"] = "ambiguous"
    # Phase C: Gemini describes item photos (once per set of photos).
    ai_photo_descriptions: bool = True
    # Category slugs whose photos are NEVER sent to an AI service (personal data).
    ai_skip_photo_categories: str = "id-cards-documents"
    # Never make more AI API calls than this per day (stays inside free quotas).
    ai_daily_call_limit: int = 500

    # Phase D: owner detection on found ID cards. The text on the photo is
    # read on OUR server (local OCR, no AI service sees the photo); a roll
    # number or an IITK email address found on it is used to email the owner.
    id_card_ocr: bool = True
    # Category slugs that always get scanned, and whose photos are only
    # shown to the reporter and to security staff.
    id_card_categories: str = "id-cards-documents"
    # Only email addresses on these domains are emailed (comma-separated).
    owner_email_domains: str = "iitk.ac.in"
    # Where a roll number with no registered user gets emailed. Empty = don't.
    roll_email_pattern: str = "{roll}@iitk.ac.in"

    @property
    def database_url(self) -> URL:
        """Build the connection URL from the parts above.

        URL.create escapes special characters in the password
        (like @ or #), which would break a hand-written URL string.
        "postgresql+psycopg" means: PostgreSQL, using the psycopg 3 driver.
        """
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    """Read the settings once and reuse them (lru_cache remembers the result)."""
    return Settings()
