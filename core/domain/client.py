from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Where a request came from, as far as the HTTP layer knows: stored on sessions and audit entries."""

    user_agent: str | None = None
    ip_address: str | None = None
