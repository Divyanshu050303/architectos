"""Text checks shared by every aggregate that stores user-written text."""

import unicodedata


def has_forbidden_characters(value: str, allowed: set[str] | frozenset[str] = frozenset()) -> bool:
    """Control and format characters (Cc, Cf), e.g. NUL or bidi overrides, other than ``allowed``."""
    return any(unicodedata.category(c) in {"Cc", "Cf"} and c not in allowed for c in value)
