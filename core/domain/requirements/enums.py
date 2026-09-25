from enum import StrEnum


class RequirementType(StrEnum):
    """The engineering concern a requirement constrains. Deliberately small: each type is something
    a future engine (capacity, validation, simulation, cost, reliability) consumes."""

    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    CAPACITY = "capacity"
    PERFORMANCE = "performance"
    AVAILABILITY = "availability"
    RELIABILITY = "reliability"
    SECURITY = "security"
    DATA = "data"
    COMPLIANCE = "compliance"
    OPERATIONAL = "operational"
    COST = "cost"


class RequirementStatus(StrEnum):
    """draft -> active -> satisfied | invalid | deprecated (transitions are enforced by the domain)."""

    DRAFT = "draft"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    INVALID = "invalid"
    DEPRECATED = "deprecated"


class RequirementPriority(StrEnum):
    """How much the requirement matters. Not to be confused with confidence."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RequirementSource(StrEnum):
    """Where the requirement came from. AI-sourced requirements are never authoritative by default:
    they start as drafts and carry a confidence."""

    USER = "user"
    AI = "ai"
