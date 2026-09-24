"""Opaque secrets: emailed tokens (verification, password reset, invitations) and refresh tokens.

A token is 32 random bytes (256 bits) encoded URL-safe; only its SHA-256 digest is stored.
A fast hash is correct here: unlike passwords, the input has full entropy, so it cannot be
brute-forced, and lookups by digest can use a unique index.
"""

import hashlib
import secrets
import uuid

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def format_refresh_token(session_id: uuid.UUID, secret: str) -> str:
    """ "<session id>.<secret>": the id makes lookup a primary-key read; only the secret is hashed."""
    return f"{session_id}.{secret}"


def parse_refresh_token(raw: str) -> tuple[uuid.UUID, str] | None:
    if len(raw) > MAX_TOKEN_LENGTH:
        return None
    session_part, dot, secret = raw.partition(".")
    if not dot or not secret:
        return None
    try:
        return uuid.UUID(session_part), secret
    except ValueError:
        return None
