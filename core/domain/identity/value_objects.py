"""Normalization of user-supplied identity values.

Email policy: surrounding whitespace is trimmed and the whole address is lower-cased. Nothing
else is rewritten (no removal of dots or "+tags"), because those are meaningful to some mail
providers. The database additionally enforces case-insensitive uniqueness.
"""

import unicodedata

from email_validator import EmailNotValidError, validate_email

from .errors import InvalidEmail, InvalidName

MAX_EMAIL_LENGTH = 320
MAX_NAME_LENGTH = 80


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
