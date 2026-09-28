"""The constraint model (ARCH-COMP-001, phase 3): typed constraints on Architecture IR properties —
hard, configurable, conditional, recommended, unsupported, unknown — never promoted beyond what the
source says; every stated limit documented; the catalog's specified entries cite what they claim."""

from decimal import Decimal
from typing import Any

import pytest

from core.domain.components.constraints import Comparison, Condition, Constraint, ConstraintType
from core.domain.components.entities import Provenance, ProvenanceKind, SupportStatus
from core.domain.components.errors import InvalidSpecification
from core.domain.components.specifications import ComponentSpecification
from core.domain.validation.results import Severity
from persistence.component_catalog import default_catalog
from tests.unit.components.test_component_specifications import DOCUMENTED, spec

DOCS = Provenance(ProvenanceKind.DOCUMENTED, ("docs",))


def constraint(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "connections_limit",
        "type": "hard_limit",
        "description": "At most 5,000 connections.",
        "property": "max_connections",
        "comparison": "at_most",
        "limit": 5000,
        "severity": "high",
        "provenance": DOCUMENTED,
    }
    data.update(overrides)
    return data


def refused(**overrides: Any) -> list[str]:
    with pytest.raises(InvalidSpecification) as error:
        ComponentSpecification.from_dict(spec(constraints=[constraint(**overrides)]))
    return list(error.value.details["fields"])


def test_a_documented_limit_is_part_of_the_specification() -> None:
    specification = ComponentSpecification.from_dict(spec(constraints=[constraint()]))
    [limit] = specification.constraints
    assert (limit.type, limit.comparison, limit.limit) == (
        ConstraintType.HARD_LIMIT,
        Comparison.AT_MOST,
        Decimal(5000),
    )
    assert "constraints.connections_limit" in dict(specification.claims())
    assert ComponentSpecification.from_dict(specification.to_dict()) == specification


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        # a limit rests on its documentation: never inferred, estimated or assumed
        ({"provenance": {"kind": "inferred", "assumptions": ["Seems right."]}}, "provenance"),
        ({"provenance": {"kind": "estimated", "model": "m@1"}}, "provenance"),
        # a recommendation, or a default that can be raised, is never as severe as an impossibility
        ({"type": "recommended_range", "severity": "high"}, "severity"),
        ({"type": "configurable_limit", "severity": "critical"}, "severity"),
        # a conditional limit states its conditions; the others hold unconditionally
        ({"type": "conditional_limit"}, "conditions"),
        ({"conditions": [{"property": "replicas", "values": [1]}]}, "conditions"),
        # an unknown limit states no value and rests on no evidence
        ({"type": "unknown", "provenance": {"kind": "unknown"}}, "comparison"),
        ({"type": "unknown", "comparison": None, "limit": None}, "provenance"),
        # the comparison fits the property and says what it compares with
        ({"property": "eviction_policy", "limit": None, "comparison": "at_most"}, "comparison"),
        ({"limit": None}, "limit"),
        ({"limit": "2.5"}, "limit"),  # max_connections is a whole number
        ({"limit": -1}, "limit"),
        ({"comparison": "between", "limit": None, "minimum": 10, "maximum": 5}, "maximum"),
        ({"comparison": "one_of", "limit": None, "values": []}, "values"),
        ({"type": "unsupported_configuration", "comparison": "at_most"}, "comparison"),
    ],
)
def test_constraints_are_never_promoted_or_invented(overrides: dict[str, Any], field: str) -> None:
    assert f"constraints[0].{field}" in refused(**overrides)  # among every field at fault


def test_list_constraints_use_the_ir_properties_values() -> None:
    allowed = Constraint(
        "policies",
        ConstraintType.UNSUPPORTED_CONFIGURATION,
        "Random eviction is not supported here.",
        "eviction_policy",
        Severity.MEDIUM,
        DOCS,
        Comparison.NOT_ONE_OF,
        values=("allkeys_random",),
    )
    assert allowed.values == ("allkeys_random",)
    with pytest.raises(InvalidSpecification):  # not a value the IR defines for the property
        Constraint("p", ConstraintType.HARD_LIMIT, "d", "eviction_policy", Severity.LOW, DOCS,
                   Comparison.ONE_OF, values=("coin_flip",))  # fmt: skip
    assert "constraints[0].values" in refused(comparison="at_most", limit=5000, values=[1])
    assert refused(limit=0.5) == ["constraints[0]"]  # exact numbers only


def test_conditions_say_when_a_limit_holds() -> None:
    condition = Condition("persistence", ("snapshot", "append_only"))
    assert (condition.holds("snapshot"), condition.holds("none"), condition.holds(None)) == (
        True,
        False,
        None,
    )
    with pytest.raises(InvalidSpecification):
        Condition("persistence", ("sometimes",))
    conditional = constraint(type="conditional_limit", conditions=[{"property": "replicas", "values": [1]}])
    assert ComponentSpecification.from_dict(spec(constraints=[conditional])).constraints[0].conditions


def test_a_constraint_fits_the_specification_it_belongs_to() -> None:
    queue_only = spec(constraints=[constraint(property="partitions", limit=10)])  # not a database property
    with pytest.raises(InvalidSpecification) as error:
        ComponentSpecification.from_dict(queue_only)
    assert error.value.details["fields"] == ["constraints.connections_limit"]
    other_version = spec(technology_versions=["16"], constraints=[constraint(technology_versions=["9.6"])])
    with pytest.raises(InvalidSpecification):
        ComponentSpecification.from_dict(other_version)
    identity = {
        "id",
        "version",
        "name",
        "category",
        "technology",
        "node_kinds",
        "description",
        "provider",
        "sources",
    }
    planned = {k: v for k, v in spec().items() if k in identity}
    with pytest.raises(InvalidSpecification):  # a planned entry claims nothing
        ComponentSpecification.from_dict(
            planned | {"support_status": "planned", "constraints": [constraint()]}
        )


def test_a_new_optional_section_changes_no_existing_hash() -> None:
    without = ComponentSpecification.from_dict(spec())
    empty = ComponentSpecification.from_dict(spec(constraints=[]))
    assert without.content_hash == empty.content_hash


def test_the_specified_catalog_entries_cite_what_they_claim() -> None:
    catalog = default_catalog()
    statuses = {s.id: s.support_status for s in catalog.list()}
    supported = {i for i, s in statuses.items() if s is SupportStatus.SUPPORTED}
    assert supported == {
        "databases/postgresql",
        "databases/redis",
        "messaging/kafka",
        "messaging/aws-sqs",
        "compute/aws-lambda",
    }
    assert {i for i, s in statuses.items() if s is SupportStatus.PARTIAL} == {"storage/aws-s3"}
    for entry in catalog.list():
        sources = {s.id: s for s in entry.sources}
        for path, provenance in entry.claims():
            assert provenance.kind in {
                ProvenanceKind.DOCUMENTED,
                ProvenanceKind.INFERRED,
                ProvenanceKind.UNKNOWN,
            }
            for cited in provenance.sources:
                assert sources[cited].retrieved is not None, (entry.id, path)  # checked against the source
        for limit in entry.constraints:
            assert limit.provenance.kind is ProvenanceKind.DOCUMENTED
        if entry.support_status is not SupportStatus.PLANNED:
            assert entry.version == 2  # version 1 (planned) stays readable
            assert catalog.get(entry.id, 1).support_status is SupportStatus.PLANNED


def test_the_documented_limits_are_the_sources_values() -> None:
    catalog = default_catalog()
    [retention] = catalog.get("messaging/aws-sqs").constraints
    assert (retention.minimum, retention.maximum) == (Decimal(60), Decimal(1_209_600))
    [memory] = catalog.get("compute/aws-lambda").constraints
    assert (memory.minimum, memory.maximum) == (Decimal(128 * 1024**2), Decimal(10_240 * 1024**2))
    message = next(c for c in catalog.get("messaging/aws-sqs").capacity if c.id == "message_size")
    assert message.value == Decimal(1_048_576)
    postgres = catalog.get("databases/postgresql")
    connections = next(f for f in postgres.configuration if f.property == "max_connections")
    assert connections.default is None  # "typically 100, but might be less": not a default to assume
