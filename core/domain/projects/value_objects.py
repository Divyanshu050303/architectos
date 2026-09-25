"""Project names, descriptions, slugs and settings: normalization and validation.

Slug rule (deterministic): NFKD-decompose, drop anything that is not ASCII (accents are dropped,
"Café" -> "cafe"), lower-case, turn every run of other characters into one hyphen, trim hyphens,
cap at 63 characters on a hyphen boundary. A slug given by the client is only trimmed and
lower-cased, then must already match the format; it is never silently rewritten.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from core.domain.text import has_forbidden_characters

from .errors import InvalidProjectDescription, InvalidProjectName, InvalidProjectSettings, InvalidProjectSlug

MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2000
MAX_SLUG_LENGTH = 63
SLUG_FORMAT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_ALLOWED_DESCRIPTION_CONTROLS = {"\n", "\t"}


def normalize_project_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not name or len(name) > MAX_NAME_LENGTH or has_forbidden_characters(name):
        raise InvalidProjectName
    return name


def normalize_project_description(raw: str) -> str:
    description = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").strip())
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidProjectDescription
    if has_forbidden_characters(description, _ALLOWED_DESCRIPTION_CONTROLS):
        raise InvalidProjectDescription(details={"reason": "control_characters"})
    return description


def slugify(name: str) -> str:
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_only.lower()).strip("-")
    if len(slug) > MAX_SLUG_LENGTH:
        slug = _truncate_on_boundary(slug)
    if not slug:
        # e.g. a name written entirely in a non-Latin script: the client must choose a slug.
        raise InvalidProjectSlug(details={"reason": "cannot_derive_from_name"})
    return slug


def _truncate_on_boundary(slug: str) -> str:
    """Cut to MAX_SLUG_LENGTH without leaving half a word, unless a single word is itself too long."""
    cut = slug[:MAX_SLUG_LENGTH]
    if slug[MAX_SLUG_LENGTH] == "-" or "-" not in cut:
        return cut.strip("-")  # the cut falls between words, or there is no boundary to use
    return cut.rsplit("-", 1)[0]


def normalize_slug(raw: str) -> str:
    slug = raw.strip().lower()
    if len(slug) > MAX_SLUG_LENGTH or not SLUG_FORMAT.fullmatch(slug):
        raise InvalidProjectSlug
    return slug


class CloudProvider(StrEnum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    """Project-wide defaults for the analysis engines. Stored as a JSON object with snake_case keys;
    unknown keys are refused so settings cannot become an untyped bag."""

    cloud_provider: CloudProvider | None = None
    currency: str = "USD"  # ISO 4217, used by the cost engine

    def __post_init__(self) -> None:
        if not _CURRENCY.fullmatch(self.currency):
            raise InvalidProjectSettings(details={"setting": "currency"})

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        unknown = set(raw) - {"cloud_provider", "currency"}
        if unknown:
            raise InvalidProjectSettings(details={"setting": sorted(unknown)[0], "reason": "unknown"})
        provider = raw.get("cloud_provider")
        try:
            cloud_provider = CloudProvider(provider) if provider is not None else None
        except ValueError:
            raise InvalidProjectSettings(details={"setting": "cloud_provider"}) from None
        currency = raw.get("currency", "USD")
        if not isinstance(currency, str):
            raise InvalidProjectSettings(details={"setting": "currency"})
        return cls(cloud_provider=cloud_provider, currency=currency.strip().upper())

    def to_dict(self) -> dict[str, Any]:
        return {
            "cloud_provider": self.cloud_provider.value if self.cloud_provider else None,
            "currency": self.currency,
        }
