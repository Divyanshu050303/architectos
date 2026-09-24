import unicodedata

from .errors import InvalidOrganizationName

MAX_ORGANIZATION_NAME_LENGTH = 100


def normalize_organization_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not name or len(name) > MAX_ORGANIZATION_NAME_LENGTH:
        raise InvalidOrganizationName
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in name):
        raise InvalidOrganizationName
    return name
