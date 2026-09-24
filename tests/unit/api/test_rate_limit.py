import logging
from datetime import timedelta

import pytest

from apps.api.middleware.rate_limit import POLICIES, Hit, InMemoryRateLimiter, RateLimited, RateLimits


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingLimiter(InMemoryRateLimiter):
    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str] = []

    async def hit(self, key: str, window: timedelta) -> Hit:
        self.keys.append(key)
        return await super().hit(key, window)


class BrokenLimiter:
    async def hit(self, key: str, window: timedelta) -> Hit:
        raise ConnectionError("redis down")

    async def close(self) -> None:
        return None


async def test_window_counts_and_resets() -> None:
    clock = Clock()
    limiter = InMemoryRateLimiter(now=clock)
    assert [(await limiter.hit("k", timedelta(seconds=60))).count for _ in range(3)] == [1, 2, 3]
    clock.now += 59
    assert (await limiter.hit("k", timedelta(seconds=60))).retry_after == 1
    clock.now += 1
    assert (await limiter.hit("k", timedelta(seconds=60))).count == 1


async def test_enforce_refuses_after_the_limit_with_retry_after() -> None:
    limits = RateLimits(InMemoryRateLimiter(), client_ip="203.0.113.1", enabled=True)
    for _ in range(POLICIES["register"]["ip"].limit):
        await limits.enforce("register")
    with pytest.raises(RateLimited) as raised:
        await limits.enforce("register")
    assert raised.value.retry_after > 0
    assert raised.value.details == {"retryAfter": raised.value.retry_after}


async def test_email_dimension_is_normalized_and_independent_of_ip() -> None:
    limiter = InMemoryRateLimiter()
    for i in range(POLICIES["login"]["email"].limit):
        # A different IP each time: the per-email counter still accumulates.
        await RateLimits(limiter, client_ip=f"198.51.100.{i}", enabled=True).enforce(
            "login", email="Ada@Example.com"
        )
    with pytest.raises(RateLimited):
        await RateLimits(limiter, client_ip="192.0.2.1", enabled=True).enforce(
            "login", email=" ada@example.com "
        )


async def test_keys_never_contain_raw_identifiers() -> None:
    limiter = RecordingLimiter()
    await RateLimits(limiter, client_ip="203.0.113.7", enabled=True).enforce("login", email="ada@example.com")
    assert len(limiter.keys) == 2
    assert all("ada" not in key and "203.0.113.7" not in key for key in limiter.keys)


async def test_disabled_limits_never_touch_the_store() -> None:
    limiter = RecordingLimiter()
    await RateLimits(limiter, client_ip="203.0.113.7", enabled=False).enforce(
        "login", email="ada@example.com"
    )
    assert limiter.keys == []


async def test_store_failure_fails_open_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="architectos.security")
    await RateLimits(BrokenLimiter(), client_ip="203.0.113.7", enabled=True).enforce("login", email="a@b.co")
    assert "rate limiter unavailable" in caplog.text


def test_every_policy_is_sane() -> None:
    for action, rules in POLICIES.items():
        assert rules, action
        for dimension, rule in rules.items():
            assert dimension in {"ip", "email", "user"}
            assert rule.limit > 0
            assert rule.window > timedelta(0)
