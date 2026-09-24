import pytest

from core.domain.identity.errors import InvalidEmail, InvalidName
from core.domain.identity.value_objects import normalize_email, normalize_name

RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)  # built at runtime so the source has no invisible characters


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Divyanshu@Example.com", "divyanshu@example.com"),
        ("  ada@example.com\t", "ada@example.com"),
        ("ADA+tag@EXAMPLE.COM", "ada+tag@example.com"),
        ("first.last@example.co.uk", "first.last@example.co.uk"),
    ],
)
def test_email_is_trimmed_and_lower_cased(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


def test_email_semantics_are_otherwise_preserved() -> None:
    # Dots and +tags are meaningful to some providers; the policy never rewrites them.
    assert normalize_email("f.i.r.s.t+x@example.com") == "f.i.r.s.t+x@example.com"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "no-at-sign",
        "@example.com",
        "ada@",
        "ada@@example.com",
        "ada @example.com",
        "a" * 310 + "@x.com",
    ],
)
def test_invalid_emails_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidEmail):
        normalize_email(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Ada Lovelace", "Ada Lovelace"), ("  Ada   Lovelace ", "Ada Lovelace"), ("Zoë", "Zoë")],
)
def test_names_are_trimmed_and_collapsed(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "x" * 81, "Ada" + RIGHT_TO_LEFT_OVERRIDE + "ecalevoL", "Ada\x00"])
def test_invalid_names_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidName):
        normalize_name(raw)
