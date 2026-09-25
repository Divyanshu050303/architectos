"""Rate limiting for security-sensitive endpoints.

Fixed-window counters behind the ``RateLimiter`` interface: Redis in production (shared by every
API process; one atomic Lua call per check), in memory for development and tests. Keys hold a
SHA-256 digest of the IP, email or user id, never the raw value.

If the store fails, the request is allowed (fail open) and the failure is logged: a Redis outage
must not lock every user out. Argon2's cost still throttles password guessing meanwhile.
"""

import hashlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from redis.asyncio import Redis

from core.domain.errors import DomainError

logger = logging.getLogger("architectos.security")


class RateLimited(DomainError):
    code = "rate_limited"
    message = "Too many attempts. Try again later."

    def __init__(self, retry_after: int) -> None:
        super().__init__(details={"retryAfter": retry_after})
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Rule:
    limit: int
    window: timedelta


@dataclass(frozen=True, slots=True)
class Hit:
    count: int
    retry_after: int  # seconds until the window resets


class RateLimiter(Protocol):
    async def hit(self, key: str, window: timedelta) -> Hit: ...

    async def close(self) -> None: ...


class InMemoryRateLimiter:
    """Single-process only: counters are not shared between API workers."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._counters: dict[str, tuple[int, float]] = {}

    async def hit(self, key: str, window: timedelta) -> Hit:
        now = self._now()
        count, expires = self._counters.get(key, (0, 0.0))
        if now >= expires:
            count, expires = 0, now + window.total_seconds()
        count += 1
        self._counters[key] = (count, expires)
        return Hit(count=count, retry_after=max(1, math.ceil(expires - now)))

    async def close(self) -> None:
        self._counters.clear()


_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('PTTL', KEYS[1])}
"""


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        self._redis: Redis = Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._script = self._redis.register_script(_INCREMENT)

    async def hit(self, key: str, window: timedelta) -> Hit:
        count, ttl_ms = await self._script(keys=[key], args=[int(window.total_seconds() * 1000)])
        return Hit(count=int(count), retry_after=max(1, math.ceil(int(ttl_ms) / 1000)))

    async def close(self) -> None:
        await self._redis.aclose()


# Action -> dimension -> rule. Dimensions: "ip", "email", "user".
POLICIES: dict[str, dict[str, Rule]] = {
    "register": {"ip": Rule(10, timedelta(hours=1))},
    "login": {"ip": Rule(30, timedelta(minutes=5)), "email": Rule(10, timedelta(minutes=15))},
    "refresh": {"ip": Rule(120, timedelta(minutes=1))},
    "verify_email": {"ip": Rule(30, timedelta(hours=1))},
    "resend_verification": {"ip": Rule(10, timedelta(hours=1)), "email": Rule(5, timedelta(hours=1))},
    "forgot_password": {"ip": Rule(10, timedelta(hours=1)), "email": Rule(5, timedelta(hours=1))},
    "reset_password": {"ip": Rule(30, timedelta(hours=1))},
    "confirm_password": {"user": Rule(10, timedelta(hours=1))},
    "create_organization": {"user": Rule(20, timedelta(days=1))},
    "create_project": {"user": Rule(100, timedelta(hours=1))},
    "create_requirement": {"user": Rule(500, timedelta(hours=1))},
    "create_requirement_set": {"user": Rule(100, timedelta(hours=1))},
    "create_invitation": {"user": Rule(50, timedelta(hours=1))},
    "accept_invitation": {"ip": Rule(30, timedelta(hours=1))},
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class RateLimits:
    """Per-request helper: ``await limits.enforce("login", email=body.email)``."""

    def __init__(self, limiter: RateLimiter, *, client_ip: str | None, enabled: bool) -> None:
        self._limiter = limiter
        self._client_ip = client_ip or "unknown"
        self._enabled = enabled

    async def enforce(self, action: str, *, email: str | None = None, user_id: object | None = None) -> None:
        if not self._enabled:
            return
        values = {"ip": self._client_ip, "email": email.strip().lower() if email else None, "user": user_id}
        for dimension, rule in POLICIES[action].items():
            value = values[dimension]
            if value is None:
                continue
            key = f"rl:{action}:{dimension}:{_digest(str(value))}"
            try:
                hit = await self._limiter.hit(key, rule.window)
            except Exception:
                logger.exception("rate limiter unavailable; allowing request", extra={"action": action})
                return
            if hit.count > rule.limit:
                logger.warning(
                    "rate limit exceeded",
                    extra={"action": action, "dimension": dimension, "retry_after": hit.retry_after},
                )
                raise RateLimited(hit.retry_after)
