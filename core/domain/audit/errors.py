# The pagination error is shared by every list endpoint; re-exported for audit imports.
from core.domain.pagination import InvalidCursor

__all__ = ["InvalidCursor"]
