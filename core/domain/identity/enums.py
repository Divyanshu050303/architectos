from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    # Blocked by an operator: cannot sign in; data kept.
    DISABLED = "disabled"
    # Account deleted by its owner: personal data scrubbed, row kept so audit references resolve.
    DELETED = "deleted"


class SessionRevocationReason(StrEnum):
    LOGOUT = "logout"
    USER_REVOKED = "user_revoked"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105 — revocation reason, not a credential
    PASSWORD_RESET = "password_reset"  # noqa: S105 — revocation reason, not a credential
    # A rotated-out refresh token was presented again: the session is assumed stolen.
    TOKEN_REUSE = "token_reuse"  # noqa: S105 — revocation reason, not a credential
    ACCOUNT_DELETED = "account_deleted"
