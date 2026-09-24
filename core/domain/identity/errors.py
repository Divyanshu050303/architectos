from core.domain.errors import DomainError


class InvalidEmail(DomainError):
    code = "invalid_email"
    message = "Enter a valid email address."


class InvalidName(DomainError):
    code = "invalid_name"
    message = "Enter a name between 1 and 80 characters."


class WeakPassword(DomainError):
    """``details`` lists every failed rule (see PasswordIssue) so a client can show them all at once."""

    code = "weak_password"
    message = "Choose a stronger password."


class EmailAlreadyRegistered(DomainError):
    """Raised by the repository on a duplicate email. Registration never surfaces it to clients."""

    code = "email_taken"
    message = "An account with this email already exists."


class InvalidToken(DomainError):
    """Unknown, already used or superseded. Deliberately does not say which."""

    code = "invalid_token"
    message = "This link is invalid or has already been used."


class TokenExpired(DomainError):
    code = "token_expired"
    message = "This link has expired. Request a new one."


class InvalidCredentials(DomainError):
    """Wrong password and unknown email are deliberately the same error."""

    code = "invalid_credentials"
    message = "Email or password is incorrect."


class AccountDisabled(DomainError):
    """Only ever raised after the correct password, so it reveals nothing to a guesser."""

    code = "account_disabled"
    message = "This account has been disabled."


class InvalidRefreshToken(DomainError):
    code = "invalid_refresh_token"
    message = "Your session is no longer valid. Sign in again."


class SessionExpired(DomainError):
    code = "session_expired"
    message = "Your session has expired. Sign in again."


class SessionRevoked(DomainError):
    """An access token whose session was revoked or expired, or whose user was disabled."""

    code = "session_revoked"
    message = "Your session is no longer valid. Sign in again."


class RefreshConflict(DomainError):
    """The refresh token was rotated moments ago by a concurrent request (another tab).
    Nothing was revoked; the client should retry with the cookie it now holds."""

    code = "refresh_conflict"
    message = "Your session was refreshed by another request. Retry."


class SessionNotFound(DomainError):
    """No active session with this id belongs to the caller (someone else's id looks the same)."""

    code = "session_not_found"
    message = "Session not found."
