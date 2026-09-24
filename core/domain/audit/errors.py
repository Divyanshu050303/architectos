from core.domain.errors import DomainError


class InvalidCursor(DomainError):
    code = "invalid_cursor"
    message = "The pagination cursor is invalid."
