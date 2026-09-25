from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    # Read-only: only restore (or, from here, delete) is allowed.
    ARCHIVED = "archived"
