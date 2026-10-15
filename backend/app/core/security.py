"""Small security helpers: making random codes and tokens, and hashing them.

Why hash? The database never stores a login code or session token as-is,
only its hash (a one-way scrambled fingerprint). If someone ever got a
copy of the database, they still couldn't sign in as anyone.

Why HMAC with SECRET_KEY instead of a plain hash? A 6-digit code has only
a million possible values, so a plain hash could be reversed by trying
them all. HMAC mixes in our secret key, which an attacker doesn't have.
"""

import hashlib
import hmac
import secrets

from app.core.config import get_settings


def generate_login_code() -> str:
    """A random 6-digit code, e.g. "042917". `secrets` (not `random`) is
    designed for security: its values can't be predicted."""
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_session_token() -> str:
    """A long random string (256 bits) stored in the browser's cookie."""
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def secrets_match(plain: str, hashed: str) -> bool:
    """Compare in constant time, so an attacker can't learn how many
    characters were correct from how long the comparison took."""
    return hmac.compare_digest(hash_secret(plain), hashed)
