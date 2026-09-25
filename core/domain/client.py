from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Where a request came from, as far as the HTTP layer knows: stored on sessions and audit
    entries; the request id correlates event logs with the request log."""

    user_agent: str | None = None
    ip_address: str | None = None
    request_id: str | None = None
