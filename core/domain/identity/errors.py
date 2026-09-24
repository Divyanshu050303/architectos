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
