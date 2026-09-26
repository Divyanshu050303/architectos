"""Workload profiles (Milestone 7, phase 1): typed, unit-aware, validated per workload type."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.domain.capacity.errors import InvalidWorkload
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadAssumption, WorkloadProfile, WorkloadType

RPS = "requests/second"


def api(**overrides: Any) -> WorkloadProfile:
    fields: dict[str, Any] = {
        "name": "Checkout peak",
        "type": WorkloadType.REQUEST_RESPONSE,
        "peak_rate": Quantity.of("2000", RPS),
        "average_rate": Quantity.of("600", RPS),
        "read_ratio": "0.8",
        "request_payload": Quantity.of("2", "KB"),
        "response_payload": Quantity.of("8", "KB"),
    }
    return WorkloadProfile(**(fields | overrides))


def refused(build: Any) -> dict[str, Any]:
    with pytest.raises(InvalidWorkload) as raised:
        build()
    details: dict[str, Any] = raised.value.details
    return details


def test_a_request_response_profile() -> None:
    profile = api(
        assumptions=(WorkloadAssumption("peak_factor", "Peak is 3x the average.", Quantity.of("3", "ratio")),)
    )
    assert profile.design_rate == 2000
    assert profile.rate_dimension.value == "request_rate"
    assert profile.to_dict()["peak_rate"] == {"value": "2000", "unit": RPS}
    assert WorkloadProfile.from_dict(profile.to_dict()) == profile


def test_rates_in_any_unit_of_the_dimension() -> None:
    assert api(peak_rate=Quantity.of("120000", "requests/minute"), average_rate=None).design_rate == 2000


def test_an_event_stream_and_a_batch() -> None:
    events = WorkloadProfile(
        "Orders", WorkloadType.EVENT_STREAM, peak_rate=Quantity.of("500", "events/second")
    )
    assert events.design_rate == 500
    batch = WorkloadProfile(
        "Nightly export", WorkloadType.BATCH, batch_size=36_000, batch_interval=Quantity.of("1", "h")
    )
    assert batch.design_rate == 10  # 36,000 records per hour


@pytest.mark.parametrize(
    ("build", "field", "reason"),
    [
        (lambda: api(peak_rate=None), "peak_rate", "required"),
        (lambda: api(peak_rate=Quantity.of("0", RPS)), "peak_rate", "must_be_positive"),
        (lambda: api(average_rate=Quantity.of("3000", RPS)), "average_rate", "above_peak"),
        (lambda: api(peak_rate=Quantity.of("5", "MB/s")), "peak_rate", "wrong_dimension"),
        (
            lambda: api(peak_rate=Quantity.of("5", "events/second"), average_rate=None),
            "peak_rate",
            "wrong_dimension_for_type",
        ),
        (lambda: api(read_ratio="1.2"), "read_ratio", "out_of_range"),
        (lambda: api(read_ratio="-0.1"), "read_ratio", "negative"),
        (lambda: api(read_ratio="abc"), "read_ratio", "not_a_number"),
        (lambda: api(request_payload=Quantity.of("1", "s")), "request_payload", "wrong_dimension"),
        (lambda: api(concurrent_users=0), "concurrent_users", "not_a_positive_count"),
        (lambda: api(concurrent_users=-5), "concurrent_users", "not_a_positive_count"),
        (lambda: api(concurrent_users=True), "concurrent_users", "not_a_positive_count"),
        (lambda: api(target_utilization="0"), "target_utilization", "out_of_range"),
        (lambda: api(target_utilization="1.5"), "target_utilization", "out_of_range"),
        (lambda: api(growth="0"), "growth", "out_of_range"),
        (lambda: api(growth="-2"), "growth", "negative"),
        (lambda: api(batch_size=10), "batch_size", "not_applicable_to_request_response"),
        (lambda: api(name=" "), "name", "invalid_text"),
        (lambda: api(name=None), "name", "required"),
        (
            lambda: WorkloadProfile(
                "E",
                WorkloadType.EVENT_STREAM,
                peak_rate=Quantity.of("5", "events/second"),
                read_ratio=Decimal("0.5"),
            ),
            "read_ratio",
            "not_applicable_to_event_stream",
        ),
        (
            lambda: WorkloadProfile("B", WorkloadType.BATCH, batch_interval=Quantity.of("1", "h")),
            "batch_size",
            "required",
        ),
        (lambda: WorkloadProfile("B", WorkloadType.BATCH, batch_size=5), "batch_interval", "required"),
        (
            lambda: WorkloadProfile(
                "B",
                WorkloadType.BATCH,
                batch_size=5,
                batch_interval=Quantity.of("1", "h"),
                peak_rate=Quantity.of("1", RPS),
            ),
            "peak_rate",
            "not_applicable_to_batch",
        ),
        (
            lambda: WorkloadProfile(
                "B", WorkloadType.BATCH, batch_size=5, batch_interval=Quantity.of("0", "h")
            ),
            "batch_interval",
            "must_be_positive",
        ),
    ],
)
def test_invalid_profiles_are_refused(build: Any, field: str, reason: str) -> None:
    assert refused(build) == {"field": field, "reason": reason}


def test_assumptions_are_named_unique_and_sorted() -> None:
    a = WorkloadAssumption("cache_hit_ratio", "80 % of reads hit the cache.", Quantity.of("0.8", "ratio"))
    b = WorkloadAssumption("peak_factor", "Peak is 3x the average.")
    assert [x.key for x in api(assumptions=(b, a)).assumptions] == ["cache_hit_ratio", "peak_factor"]
    assert refused(lambda: api(assumptions=(b, b))) == {"field": "assumptions", "reason": "duplicate_key"}
    assert refused(lambda: WorkloadAssumption("Peak Factor", "x")) == {
        "field": "assumptions.key",
        "reason": "invalid_key",
    }


def test_from_dict_is_strict() -> None:
    data = api().to_dict()
    assert refused(lambda: WorkloadProfile.from_dict(data | {"cpu": 4})) == {
        "field": "cpu",
        "reason": "unknown_field",
    }
    assert refused(lambda: WorkloadProfile.from_dict(data | {"type": "streaming"})) == {
        "field": "type",
        "reason": "unknown_type",
    }
    bad_unit = data | {"peak_rate": {"value": "5", "unit": "rps"}}
    assert refused(lambda: WorkloadProfile.from_dict(bad_unit)) == {
        "field": "peak_rate.unit",
        "reason": "unknown_unit",
    }
    negative = data | {"peak_rate": {"value": "-5", "unit": RPS}}
    assert refused(lambda: WorkloadProfile.from_dict(negative)) == {
        "field": "peak_rate",
        "reason": "negative",
    }


def test_the_canonical_form_is_stable() -> None:
    r1, r2 = uuid.UUID(int=2), uuid.UUID(int=1)
    one = api(requirement_ids=(r1, r2, r1), read_ratio=Decimal("0.80"))
    other = api(requirement_ids=(r2, r1), read_ratio="0.8")
    assert one == other
    assert one.to_dict() == other.to_dict()
    assert one.to_dict()["requirement_ids"] == [str(r2), str(r1)]
