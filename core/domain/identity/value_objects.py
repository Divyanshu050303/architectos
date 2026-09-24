"""Normalization of user-supplied identity values.

Email policy: surrounding whitespace is trimmed and the whole address is lower-cased. Nothing
else is rewritten (no removal of dots or "+tags"), because those are meaningful to some mail
providers. The database additionally enforces case-insensitive uniqueness.
"""

import unicodedata
import uuid
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email

from .errors import InvalidAvatarUrl, InvalidEmail, InvalidName

MAX_EMAIL_LENGTH = 320
MAX_NAME_LENGTH = 80
MAX_AVATAR_URL_LENGTH = 2048
# RFC 2606 reserves .invalid: a tombstone address can never receive mail or collide with a real one.
DELETED_EMAIL_DOMAIN = "deleted.invalid"
DELETED_USER_NAME = "Deleted user"


def normalize_email(raw: str) -> str:
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_EMAIL_LENGTH:
        raise InvalidEmail
    try:
        # Syntax only: deliverability (DNS) checks would leak timing and fail offline.
        validated = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        raise InvalidEmail from None
    return validated.normalized.lower()


def normalize_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not name or len(name) > MAX_NAME_LENGTH:
        raise InvalidName
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in name):
        # Control and invisible formatting characters (e.g. RTL overrides) enable spoofing.
        raise InvalidName
    return name


def normalize_avatar_url(raw: str, *, allowed_hosts: frozenset[str] = frozenset()) -> str:
    """https only, no embedded credentials, bounded length. The web app renders this in <img src>,
    so javascript:, data: and plain http: are refused. With ``allowed_hosts`` configured, only
    those hosts (e.g. an image CDN) are accepted, which also stops third-party tracking pixels."""
    url = raw.strip()
    if not url or len(url) > MAX_AVATAR_URL_LENGTH or any(char.isspace() for char in url):
        raise InvalidAvatarUrl
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise InvalidAvatarUrl from None
    if (
        parts.scheme.lower() != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
    ):
        raise InvalidAvatarUrl
    if allowed_hosts and host not in allowed_hosts:
        raise InvalidAvatarUrl
    return url


def tombstone_email(user_id: uuid.UUID) -> str:
    return f"deleted+{user_id.hex}@{DELETED_EMAIL_DOMAIN}"
