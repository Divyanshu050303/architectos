"""What a node is: its kind, its technology and, optionally, its component-catalog entry.

The kinds are architectural roles and deliberately few; adding one is a schema change (see
docs/architecture/architecture-ir.md). Technologies are *not* enumerated here: "postgresql",
"redis" or "kafka" are identifiers, and what each technology can do (limits, supported settings)
belongs to the component catalog and the constraints engine, never to the IR.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from .errors import Violation, raise_if


class NodeKind(StrEnum):
    CLIENT = "client"  # web or mobile application, device, another system's client
    CDN = "cdn"
    LOAD_BALANCER = "load_balancer"
    GATEWAY = "gateway"  # API gateway, ingress, reverse proxy
    SERVICE = "service"  # a backend service or workload serving requests
    WORKER = "worker"  # background processing, consumers, scheduled jobs
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"  # message broker, event log, queue
    STORAGE = "storage"  # object or file storage
    OBSERVABILITY = "observability"  # logging, metrics, tracing
    EXTERNAL = "external"  # a third-party service or provider outside the system
    BOUNDARY = "boundary"  # a grouping, not a component: system, network, region, account, cluster


# Technology: "postgresql", "redis", "aws-alb", "node.js"; version: "16", "7.2", "2024-05".
TECHNOLOGY_NAME = re.compile(r"^[a-z0-9][a-z0-9+._-]{0,63}$")
TECHNOLOGY_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
# A component-catalog path, e.g. "databases/postgresql" (knowledge/components/databases/postgresql.yaml).
COMPONENT_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}(?:/[a-z0-9][a-z0-9_.-]{0,63}){0,3}$")


@dataclass(frozen=True, slots=True)
class Technology:
    name: str
    version: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.name, str):
            object.__setattr__(self, "name", self.name.strip().lower())
        if isinstance(self.version, str):
            object.__setattr__(self, "version", self.version.strip() or None)
        raise_if(self.problems())

    def problems(self) -> list[Violation]:
        problems: list[Violation] = []
        if not isinstance(self.name, str) or not TECHNOLOGY_NAME.fullmatch(self.name):
            problems.append(
                Violation(
                    "invalid_technology",
                    "The technology must be a lower-case identifier, e.g. postgresql or aws-alb.",
                    "name",
                )
            )
        if self.version is not None and (
            not isinstance(self.version, str) or not TECHNOLOGY_VERSION.fullmatch(self.version)
        ):
            problems.append(
                Violation(
                    "invalid_technology", "The version must be a short identifier, e.g. 16 or 7.2.", "version"
                )
            )
        return problems


def component_problems(value: object, field: str = "component") -> list[Violation]:
    if value is None or (isinstance(value, str) and COMPONENT_REFERENCE.fullmatch(value)):
        return []
    return [
        Violation(
            "invalid_component_reference",
            "The component reference must be a catalog path, e.g. databases/postgresql.",
            field,
        )
    ]
