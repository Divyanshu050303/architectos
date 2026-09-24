"""RedisRateLimiter against the Redis from docker-compose (make db-up)."""

import os
import uuid
from datetime import timedelta

import pytest
from redis.asyncio import Redis

from apps.api.middleware.rate_limit import RedisRateLimiter

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/15")


async def test_counts_share_a_window_and_expire() -> None:
    limiter = RedisRateLimiter(REDIS_URL)
    key = f"rl:test:{uuid.uuid4().hex}"
    try:
        hits = [await limiter.hit(key, timedelta(seconds=30)) for _ in range(3)]
        assert [h.count for h in hits] == [1, 2, 3]
        assert all(0 < h.retry_after <= 30 for h in hits)
        other = await limiter.hit(f"{key}:other", timedelta(seconds=30))
        assert other.count == 1
    finally:
        redis = Redis.from_url(REDIS_URL)
        ttl = await redis.pttl(key)
        await redis.delete(key, f"{key}:other")
        await redis.aclose()
        await limiter.close()
    assert 0 < ttl <= 30_000  # the window's expiry was set atomically with the first hit
