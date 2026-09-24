import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from apps.api.access_tokens import (
    ALGORITHM,
    AUDIENCE,
    ISSUER,
    AccessTokenCodec,
    AccessTokenExpired,
    InvalidAccessToken,
)

SECRET = "unit-test-secret-" + "x" * 64
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
USER, SESSION = uuid.uuid7(), uuid.uuid7()


@pytest.fixture
def codec() -> AccessTokenCodec:
    return AccessTokenCodec(secret=SECRET, ttl=timedelta(minutes=15))


def claims(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "typ": "access",
        "sub": str(USER),
        "sid": str(SESSION),
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=15)).timestamp()),
    }
    return base | overrides


def test_issued_tokens_decode_to_the_same_identity(codec: AccessTokenCodec) -> None:
    issued = codec.issue(user_id=USER, session_id=SESSION, now=NOW)
    decoded = codec.decode(issued.token, now=NOW + timedelta(minutes=14))

    assert (decoded.user_id, decoded.session_id) == (USER, SESSION)
    assert issued.expires_in == 900


def test_tokens_expire(codec: AccessTokenCodec) -> None:
    issued = codec.issue(user_id=USER, session_id=SESSION, now=NOW)
    with pytest.raises(AccessTokenExpired):
        codec.decode(issued.token, now=NOW + timedelta(minutes=15))


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(claims(), "another-secret-" + "y" * 32, algorithm=ALGORITHM),
        jwt.encode(claims(), SECRET, algorithm="HS512"),  # algorithm not allowed
        jwt.encode(claims(aud="someone-else"), SECRET, algorithm=ALGORITHM),
        jwt.encode(claims(iss="someone-else"), SECRET, algorithm=ALGORITHM),
        jwt.encode(claims(typ="refresh"), SECRET, algorithm=ALGORITHM),
        jwt.encode(claims(sub="not-a-uuid"), SECRET, algorithm=ALGORITHM),
        jwt.encode({k: v for k, v in claims().items() if k != "sid"}, SECRET, algorithm=ALGORITHM),
        "not.a.jwt",
        "",
    ],
    ids=[
        "wrong-key",
        "wrong-alg",
        "wrong-aud",
        "wrong-iss",
        "wrong-typ",
        "bad-sub",
        "missing-sid",
        "garbage",
        "empty",
    ],
)
def test_forged_or_foreign_tokens_are_rejected(codec: AccessTokenCodec, token: str) -> None:
    with pytest.raises(InvalidAccessToken):
        codec.decode(token, now=NOW)


def test_unsigned_tokens_are_rejected(codec: AccessTokenCodec) -> None:
    unsigned = jwt.encode(claims(), None, algorithm="none")
    with pytest.raises(InvalidAccessToken):
        codec.decode(unsigned, now=NOW)


def test_tampered_payload_is_rejected(codec: AccessTokenCodec) -> None:
    header, _, signature = codec.issue(user_id=USER, session_id=SESSION, now=NOW).token.split(".")
    other_payload = jwt.encode(claims(sub=str(uuid.uuid7())), SECRET, algorithm=ALGORITHM).split(".")[1]
    with pytest.raises(InvalidAccessToken):
        codec.decode(f"{header}.{other_payload}.{signature}", now=NOW)
