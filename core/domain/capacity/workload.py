"""The workload profile: the demand an analysis places on an architecture, stated explicitly.

Three workload types, each with the fields it needs (no field is filled in when absent):

- ``request_response``: API traffic. Required: ``peak_rate`` (a request rate). Optional: average
  rate (at most the peak), peak duration, concurrent users or connections, read ratio, request and
  response payload sizes.
- ``event_stream``: messages flowing through brokers. Required: ``peak_rate`` (an event rate).
  Optional: average rate, peak duration, request payload size (the message size).
- ``batch``: periodic jobs. Required: ``batch_size`` (records per batch) and ``batch_interval`` (a
  duration). Optional: request payload size (the record size). No rates: a batch's rate follows
  from its size and interval.

Common and optional: ``growth`` (a multiplier applied to the rates in scenarios, never silently),
``target_utilization`` (the share of capacity the operator wants to stay under, in (0, 1]),
``assumptions`` (each named, with the value it assumes, if any) and ``requirement_ids`` (capacity
requirements the profile answers, for traceability).
"""

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from core.domain.requirements.value_objects import decimal_to_str
from core.domain.text import has_forbidden_characters

from .errors import InvalidQuantity, InvalidWorkload
from .units import Dimension, Quantity, exact

MAX_NAME_LENGTH = 100
MAX_TEXT_LENGTH = 1000
MAX_ASSUMPTIONS = 50
MAX_REQUIREMENTS = 100
MAX_COUNT = 10**12  # users, connections, records: generous, and bounded
ASSUMPTION_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class WorkloadType(StrEnum):
    REQUEST_RESPONSE = "request_response"
    EVENT_STREAM = "event_stream"
    BATCH = "batch"


def _invalid(field_name: str, reason: str) -> InvalidWorkload:
    return InvalidWorkload(details={"field": field_name, "reason": reason})


def _text(value: object, field_name: str, limit: int, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip() and not required):
        if required:
            raise _invalid(field_name, "required")
        return None
    if not isinstance(value, str):
        raise _invalid(field_name, "not_text")
    clean = " ".join(value.split()) if limit <= MAX_NAME_LENGTH else value.strip()
    if not clean or len(clean) > limit or has_forbidden_characters(clean, frozenset({"\n", "\t"})):
        raise _invalid(field_name, "invalid_text")
    return clean


def _count(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_COUNT:
        raise _invalid(field_name, "not_a_positive_count")
    return value


def _ratio(value: object, field_name: str, *, zero: bool) -> Decimal | None:
    if value is None:
        return None
    try:
        ratio = exact(value, field_name)
    except InvalidQuantity as error:
        raise _invalid(field_name, error.details["reason"]) from None
    if ratio > 1 or (ratio == 0 and not zero):
        raise _invalid(field_name, "out_of_range")
    return ratio


@dataclass(frozen=True, slots=True)
class WorkloadAssumption:
    """Something the profile takes as true, e.g. peak_factor = 3 ("peak is 3x the average").
    ``value`` is optional: some assumptions are statements, not numbers."""

    key: str
    statement: str
    value: Quantity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not ASSUMPTION_KEY.fullmatch(self.key):
            raise _invalid("assumptions.key", "invalid_key")
        object.__setattr__(
            self, "statement", _text(self.statement, "assumptions.statement", 500, required=True)
        )
        if self.value is not None and not isinstance(self.value, Quantity):
            raise _invalid("assumptions.value", "not_a_quantity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "statement": self.statement,
            "value": self.value.to_dict() if self.value is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: object) -> WorkloadAssumption:
        """Strict: {key, statement, value?}, the value a {value, unit} quantity."""
        if not isinstance(raw, Mapping) or set(raw) - {"key", "statement", "value"}:
            raise _invalid("assumptions", "invalid_assumption")
        value = raw.get("value")
        try:
            parsed = Quantity.from_dict(value, "assumptions.value") if value is not None else None
        except InvalidQuantity as error:
            raise _invalid(error.details["field"], error.details["reason"]) from None
        return cls(raw.get("key"), raw.get("statement"), parsed)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    name: str
    type: WorkloadType
    description: str | None = None
    peak_rate: Quantity | None = None
    average_rate: Quantity | None = None
    peak_duration: Quantity | None = None
    concurrent_users: int | None = None
    concurrent_connections: int | None = None
    read_ratio: Decimal | None = None  # share of requests that read; writes are the rest
    request_payload: Quantity | None = None  # bytes per request, message or record
    response_payload: Quantity | None = None
    batch_size: int | None = None  # records per batch
    batch_interval: Quantity | None = None
    growth: Decimal | None = None  # multiplier for scenarios, e.g. 1.5
    target_utilization: Decimal | None = None  # in (0, 1]
    assumptions: tuple[WorkloadAssumption, ...] = ()
    requirement_ids: tuple[uuid.UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name", MAX_NAME_LENGTH, required=True))
        object.__setattr__(
            self, "description", _text(self.description, "description", MAX_TEXT_LENGTH, required=False)
        )
        if not isinstance(self.type, WorkloadType):
            raise _invalid("type", "unknown_type")
        object.__setattr__(self, "read_ratio", _ratio(self.read_ratio, "read_ratio", zero=True))
        object.__setattr__(
            self, "target_utilization", _ratio(self.target_utilization, "target_utilization", zero=False)
        )
        for name in ("concurrent_users", "concurrent_connections", "batch_size"):
            object.__setattr__(self, name, _count(getattr(self, name), name))
        self._check_growth()
        self._check_quantities()
        self._check_type()
        self._check_references()

    def _check_growth(self) -> None:
        if self.growth is None:
            return
        try:
            growth = exact(self.growth, "growth")
        except InvalidQuantity as error:
            raise _invalid("growth", error.details["reason"]) from None
        if growth == 0:
            raise _invalid("growth", "out_of_range")
        object.__setattr__(self, "growth", growth)

    def _check_quantities(self) -> None:
        dimensions = {
            "peak_rate": (Dimension.REQUEST_RATE, Dimension.EVENT_RATE),
            "average_rate": (Dimension.REQUEST_RATE, Dimension.EVENT_RATE),
            "peak_duration": (Dimension.DURATION,),
            "request_payload": (Dimension.DATA_SIZE,),
            "response_payload": (Dimension.DATA_SIZE,),
            "batch_interval": (Dimension.DURATION,),
        }
        for name, allowed in dimensions.items():
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, Quantity):
                raise _invalid(name, "not_a_quantity")
            if value.dimension not in allowed:
                raise _invalid(name, "wrong_dimension")
        for name in ("peak_rate", "batch_interval"):
            value = getattr(self, name)
            if value is not None and value.value == 0:
                raise _invalid(name, "must_be_positive")

    def _check_type(self) -> None:
        match self.type:
            case WorkloadType.REQUEST_RESPONSE | WorkloadType.EVENT_STREAM:
                expected = (
                    Dimension.REQUEST_RATE
                    if self.type is WorkloadType.REQUEST_RESPONSE
                    else Dimension.EVENT_RATE
                )
                if self.peak_rate is None:
                    raise _invalid("peak_rate", "required")
                for name in ("peak_rate", "average_rate"):
                    value = getattr(self, name)
                    if value is not None and value.dimension is not expected:
                        raise _invalid(name, "wrong_dimension_for_type")
                if self.average_rate is not None and self.average_rate.canonical > self.peak_rate.canonical:
                    raise _invalid("average_rate", "above_peak")
                self._forbid("batch_size", "batch_interval")
                if self.type is WorkloadType.EVENT_STREAM:
                    self._forbid("read_ratio", "response_payload", "concurrent_users")
            case WorkloadType.BATCH:
                if self.batch_size is None:
                    raise _invalid("batch_size", "required")
                if self.batch_interval is None:
                    raise _invalid("batch_interval", "required")
                self._forbid(
                    "peak_rate",
                    "average_rate",
                    "peak_duration",
                    "concurrent_users",
                    "concurrent_connections",
                    "response_payload",
                )

    def _forbid(self, *names: str) -> None:
        for name in names:
            if getattr(self, name) is not None:
                raise _invalid(name, f"not_applicable_to_{self.type.value}")

    def _check_references(self) -> None:
        if not isinstance(self.assumptions, tuple) or len(self.assumptions) > MAX_ASSUMPTIONS:
            raise _invalid("assumptions", "too_many")
        keys = [a.key for a in self.assumptions]
        if len(keys) != len(set(keys)):
            raise _invalid("assumptions", "duplicate_key")
        object.__setattr__(self, "assumptions", tuple(sorted(self.assumptions, key=lambda a: a.key)))
        if not isinstance(self.requirement_ids, tuple) or len(self.requirement_ids) > MAX_REQUIREMENTS:
            raise _invalid("requirement_ids", "too_many")
        if not all(isinstance(r, uuid.UUID) for r in self.requirement_ids):
            raise _invalid("requirement_ids", "invalid_reference")
        object.__setattr__(self, "requirement_ids", tuple(sorted(set(self.requirement_ids), key=str)))

    # --- derived -------------------------------------------------------------------------------

    @property
    def rate_dimension(self) -> Dimension:
        return Dimension.REQUEST_RATE if self.type is WorkloadType.REQUEST_RESPONSE else Dimension.EVENT_RATE

    @property
    def design_rate(self) -> Decimal:
        """The rate the analysis designs for, in the canonical unit per second: the peak rate, or a
        batch's records per second (size / interval), exactly."""
        if self.batch_size is not None and self.batch_interval is not None:  # a batch
            return Decimal(self.batch_size) * 1000 / self.batch_interval.canonical  # per ms -> per second
        if self.peak_rate is None:  # impossible: construction requires one or the other
            raise _invalid("peak_rate", "required")
        return self.peak_rate.canonical

    # --- serialization -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Canonical: every field present, numbers as exact strings, lists sorted."""

        def quantity(value: Quantity | None) -> dict[str, str] | None:
            return value.to_dict() if value is not None else None

        def number(value: Decimal | None) -> str | None:
            return decimal_to_str(value) if value is not None else None

        return {
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "peak_rate": quantity(self.peak_rate),
            "average_rate": quantity(self.average_rate),
            "peak_duration": quantity(self.peak_duration),
            "concurrent_users": self.concurrent_users,
            "concurrent_connections": self.concurrent_connections,
            "read_ratio": number(self.read_ratio),
            "request_payload": quantity(self.request_payload),
            "response_payload": quantity(self.response_payload),
            "batch_size": self.batch_size,
            "batch_interval": quantity(self.batch_interval),
            "growth": number(self.growth),
            "target_utilization": number(self.target_utilization),
            "assumptions": [a.to_dict() for a in self.assumptions],
            "requirement_ids": [str(r) for r in self.requirement_ids],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Strict: unknown keys are refused, quantities are {value, unit}."""
        if not isinstance(data, Mapping):
            raise _invalid("workload", "not_an_object")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise _invalid(unknown[0], "unknown_field")
        try:
            workload_type = WorkloadType(str(data.get("type")))
        except ValueError:
            raise _invalid("type", "unknown_type") from None

        def quantity(name: str) -> Quantity | None:
            raw = data.get(name)
            if raw is None:
                return None
            try:
                return Quantity.from_dict(raw, name)
            except InvalidQuantity as error:
                raise _invalid(error.details["field"], error.details["reason"]) from None

        raw_assumptions = data.get("assumptions") or []
        if not isinstance(raw_assumptions, list) or len(raw_assumptions) > MAX_ASSUMPTIONS:
            raise _invalid("assumptions", "too_many")
        assumptions = [WorkloadAssumption.from_dict(raw) for raw in raw_assumptions]
        raw_requirements = data.get("requirement_ids") or []
        if not isinstance(raw_requirements, list):
            raise _invalid("requirement_ids", "not_a_list")
        try:
            requirement_ids = tuple(uuid.UUID(str(r)) for r in raw_requirements)
        except ValueError:
            raise _invalid("requirement_ids", "invalid_reference") from None
        return cls(
            name=data.get("name"),  # type: ignore[arg-type]
            type=workload_type,
            description=data.get("description"),
            peak_rate=quantity("peak_rate"),
            average_rate=quantity("average_rate"),
            peak_duration=quantity("peak_duration"),
            concurrent_users=data.get("concurrent_users"),
            concurrent_connections=data.get("concurrent_connections"),
            read_ratio=data.get("read_ratio"),
            request_payload=quantity("request_payload"),
            response_payload=quantity("response_payload"),
            batch_size=data.get("batch_size"),
            batch_interval=quantity("batch_interval"),
            growth=data.get("growth"),
            target_utilization=data.get("target_utilization"),
            assumptions=tuple(assumptions),
            requirement_ids=requirement_ids,
        )
