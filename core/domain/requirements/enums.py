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
    """Who stated or interpreted the requirement. Only a person is authoritative: everything else
    starts as a draft that a person promotes, and machine interpretations (ai, discovery) carry a
    confidence in that interpretation."""

    USER = "user"  # typed by a person, as a structured requirement
    AI = "ai"  # interpreted by a language model
    IMPORTED = "imported"  # brought in from another tool or document
    DISCOVERY = "discovery"  # inferred from an existing system (e.g. observed traffic)
    SYSTEM = "system"  # extracted from a person's text by ArchitectOS's deterministic rules


class RequirementScope(StrEnum):
    """What the requirement applies to. ``system`` also means "not specified": a scope is never
    invented from text that does not state one."""

    SYSTEM = "system"
    SERVICE = "service"
    API = "api"
    DATABASE = "database"
    QUEUE = "queue"
    USER = "user"
    REGION = "region"
    DATA = "data"
