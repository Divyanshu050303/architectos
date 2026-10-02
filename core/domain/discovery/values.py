"""The vocabulary of discovery: what a run is, what a source is, how sure a finding is, how it maps to
the component catalog and how a person decided about it.

Discovery **gathers evidence**; it does not establish the running system. A Kubernetes manifest
declaring three replicas is evidence of the desired count in that manifest, never proof that three
pods run. Every finding therefore says how it is known (``Verification``) — and never as one
generic confidence score:

- ``observed``: directly present in the supplied source;
- ``user_provided``: entered or confirmed by a person;
- ``inferred``: derived from evidence, not declared (e.g. a database kind from an image name);
- ``estimated``: calculated from explicit inputs and a documented model;
- ``unknown``: the evidence is insufficient;
- ``unsupported``: the source or property cannot be interpreted (yet).

In the Architecture IR these become provenance (``provenance_for``): the source's provenance kind
(``terraform``, ``kubernetes``, ``file_import``), never ``verified`` (a file is not the running
system), ``inferred`` for inferred and estimated values; a person's confirmation is a
``user_edit``. Unknown and unsupported values never enter the IR as values.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from core.architecture_ir.provenance import Provenance, ProvenanceSource

from .errors import InvalidDiscoveryResult

KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")  # also a valid IR element id
CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
MAX_TEXT = 2000
MAX_REFERENCE = 500
MAX_ITEMS = 500
MAX_VALUE_DEPTH = 4
MAX_VALUE_ITEMS = 100


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"  # something was unsupported, ambiguous or unresolved
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED = frozenset(
    {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS, RunStatus.FAILED, RunStatus.CANCELLED}
)


class SourceType(StrEnum):
    KUBERNETES = "kubernetes"  # Kubernetes manifests (YAML or JSON)
    DOCKER_COMPOSE = "docker_compose"  # a Compose file
    TERRAFORM_JSON = "terraform_json"  # Terraform JSON configuration, or `terraform show -json` output
    ARCHITECTURE_JSON = "architecture_json"  # an Architecture IR document exported from ArchitectOS


class ArtifactStatus(StrEnum):
    PARSED = "parsed"  # read completely
    PARTIAL = "partial"  # read, with constructs reported unsupported
    UNSUPPORTED = "unsupported"  # a format no adapter reads (e.g. native Terraform HCL)
    FAILED = "failed"  # malformed, or over a limit


class Verification(StrEnum):
    OBSERVED = "observed"
    USER_PROVIDED = "user_provided"
    INFERRED = "inferred"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class FindingType(StrEnum):
    ENTITY = "entity"  # a declared resource, workload or service
    PROPERTY = "property"  # a declared property of one
    REFERENCE = "reference"  # an explicit reference from one to another (or to something absent)
    UNSUPPORTED = "unsupported"  # a construct the adapter does not interpret


class MappingStatus(StrEnum):
    EXACT_MATCH = "exact_match"  # the source type names one catalog component exactly
    MAPPED = "mapped"  # a deterministic rule maps it to one catalog component
    AMBIGUOUS = "ambiguous"  # several components fit: a person chooses
    UNMAPPED = "unmapped"  # no component fits (it may still be represented, without a component)
    UNSUPPORTED = "unsupported"  # the resource type is not interpreted


class EntityRole(StrEnum):
    """What a declared resource is to the architecture. Only a ``component`` can become a node; the
    others are evidence that relationships rest on (a Service routes to workloads, a workload reads a
    ConfigMap, mounts a volume, joins a network)."""

    COMPONENT = "component"
    ROUTING = "routing"  # e.g. a Kubernetes Service object
    CONFIGURATION = "configuration"  # e.g. a ConfigMap, a Secret, a security group
    VOLUME = "volume"  # e.g. a PersistentVolumeClaim, a Compose volume
    NETWORK = "network"  # e.g. a Compose network, a VPC or subnet


class RelationshipStatus(StrEnum):
    RESOLVED = "resolved"  # an explicit reference to an entity of the same discovery
    UNRESOLVED = "unresolved"  # the referenced entity is absent, or not uniquely identified


class Decision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


IR_SOURCES = {
    SourceType.KUBERNETES: ProvenanceSource.KUBERNETES,
    SourceType.DOCKER_COMPOSE: ProvenanceSource.FILE_IMPORT,
    SourceType.TERRAFORM_JSON: ProvenanceSource.TERRAFORM,
    SourceType.ARCHITECTURE_JSON: ProvenanceSource.FILE_IMPORT,
}


def provenance_for(
    verification: Verification, source: SourceType, reference: str | None
) -> Provenance | None:
    """The IR provenance of a discovered value; ``None`` when the value must not enter the IR."""
    actor = f"discovery:{source.value}"
    if verification is Verification.OBSERVED:
        return Provenance(IR_SOURCES[source], reference, actor=actor)
    if verification in {Verification.INFERRED, Verification.ESTIMATED}:
        return Provenance(IR_SOURCES[source], reference, inferred=True, actor=actor)
    if verification is Verification.USER_PROVIDED:
        return Provenance(ProvenanceSource.USER_EDIT, reference, verified=True)
    return None  # unknown and unsupported values stay out of the architecture


# --- checking helpers ------------------------------------------------------------------------------


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidDiscoveryResult(details={"fields": found})


def text(value: object, name: str, limit: int = MAX_TEXT, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    return None if isinstance(value, str) and value.strip() and len(value) <= limit else name


def key(value: object, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    return None if isinstance(value, str) and KEY.fullmatch(value) else name


def code(value: object, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    return None if isinstance(value, str) and CODE.fullmatch(value) else name


def texts(values: object, name: str, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    return None if all(text(v, name, limit) is None for v in values) else name


def items(values: object, kind: type, name: str, limit: int = MAX_ITEMS) -> str | None:
    ok = isinstance(values, tuple) and len(values) <= limit and all(isinstance(v, kind) for v in values)
    return None if ok else name


def count(value: object, name: str, *, required: bool = True, minimum: int = 0) -> str | None:
    if value is None and not required:
        return None
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= minimum
    return None if ok else name


def json_value(value: object, name: str, depth: int = 0) -> str | None:
    """A JSON value as written in the source: scalars, and bounded lists and objects of them."""
    if value is None or isinstance(value, bool | int | float | str):
        return None if not isinstance(value, str) or len(value) <= MAX_TEXT else name
    if depth >= MAX_VALUE_DEPTH:
        return name
    if isinstance(value, list | tuple):
        children: Iterable[object] = value
    elif isinstance(value, dict) and all(isinstance(k, str) for k in value):
        children = value.values()
    else:
        return name
    listed = list(children)
    if len(listed) > MAX_VALUE_ITEMS:
        return name
    return next((name for child in listed if json_value(child, name, depth + 1)), None)


def digest(prefix: str, *parts: Any) -> str:
    """A stable id from content: the same parts always give the same id."""
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
