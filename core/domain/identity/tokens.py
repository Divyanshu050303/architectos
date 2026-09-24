"""Opaque secrets sent to users (verification, password reset, invitation links).

A token is 32 random bytes (256 bits) encoded URL-safe; only its SHA-256 digest is stored.
A fast hash is correct here: unlike passwords, the input has full entropy, so it cannot be
brute-forced, and lookups by digest can use a unique index.
"""

import hashlib
import secrets

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()
