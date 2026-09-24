from enum import StrEnum


def in_values(column: str, enum: type[StrEnum]) -> str:
    """SQL CHECK expression restricting ``column`` to the enum's values (kept in sync with the domain)."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"
