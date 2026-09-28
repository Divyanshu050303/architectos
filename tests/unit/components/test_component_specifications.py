"""The component specification contract (ARCH-COMP-001, phase 1): canonical, versioned, hashed
specifications whose every claim carries provenance; capability states; configuration fields that
are Architecture IR properties; capacity values only with their basis; explicit support states;
untrusted input refused with the fields at fault."""

import copy
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import NODE_PROPERTIES
from core.domain.components.capabilities import CAPABILITIES, Capability, CapabilityState
from core.domain.components.entities import CATEGORIES, Provenance, ProvenanceKind, SupportStatus
from core.domain.components.errors import InvalidSpecification
from core.domain.components.specifications import (
    CapacityDimension,
    ComponentSpecification,
    ConfigurationField,
    Scope,
)

DOCUMENTED = {"kind": "documented", "sources": ["docs"]}


def spec(**overrides: Any) -> dict[str, Any]:
    """A supported specification, as a catalog file would state it."""
    data: dict[str, Any] = {
        "id": "databases/example-sql",
        "version": 1,
        "name": "Example SQL",
        "category": "database",
        "technology": "example-sql",
        "node_kinds": ["database"],
        "support_status": "supported",
        "description": "A relational database used by these tests.",
        "provider": {"name": "community", "service": "Example SQL"},
        "hosting": "self_hosted",
        "aliases": ["examplesql"],
        "capabilities": [
            {"id": "transactions", "state": "native", "provenance": DOCUMENTED},
            {"id": "encryption_in_transit", "state": "requires_configuration", "requires": ["ssl"],
             "provenance": DOCUMENTED},
            {"id": "full_text_search", "state": "unknown", "provenance": {"kind": "unknown"}},
        ],
        "configuration": [
            {"property": "max_connections", "required": True, "engines": ["validation", "capacity"],
             "default": 100, "provenance": DOCUMENTED},
            {"property": "replicas", "engines": ["reliability", "capacity", "cost"]},
        ],
        "capacity": [
            {"id": "connections", "unit": "connections", "scope": "instance", "property": "max_connections",
             "provenance": DOCUMENTED},
        ],
        "scaling": [
            {"id": "read_replicas", "state": "requires_configuration", "requires": ["streaming replication"],
             "affects": ["consistency", "cost"], "provenance": DOCUMENTED},
        ],
        "failure_modes": [
            {"id": "connection_exhaustion", "description": "Every connection slot is in use.",
             "impact": "New connections are refused.", "signals": ["connections"],
             "mitigations": ["A connection pool."], "provenance": DOCUMENTED},
        ],
        "signals": [
            {"id": "connections", "type": "metric", "availability": "requires_external",
             "unit": "connections", "collection": "an exporter", "provenance": DOCUMENTED},
        ],
        "security": [
            {"id": "encryption_in_transit", "state": "requires_configuration", "requires": ["ssl"],
             "property": "tls", "provenance": DOCUMENTED},
        ],
        "billing": [
            {"id": "instance_hours", "unit": "instance_hour", "driver": "replicas", "provenance": DOCUMENTED},
        ],
        "operations": [
            {"area": "backup_restore", "responsibility": "operator",
             "statement": "Backups are the operator's.", "provenance": DOCUMENTED},
        ],
        "sources": [
            {"id": "docs", "name": "Example SQL documentation", "reference": "https://example.org/docs",
             "version": "16", "retrieved": "2026-09-28"},
        ],
    }  # fmt: skip
    data.update(overrides)
    return data


def refused(data: object) -> dict[str, Any]:
    with pytest.raises(InvalidSpecification) as error:
        ComponentSpecification.from_dict(data)
    details = error.value.details
    assert isinstance(details, dict)
    return details


def test_a_specification_is_canonical_versioned_and_hashed() -> None:
    specification = ComponentSpecification.from_dict(spec())
    assert (specification.ref, specification.support_status) == (
        "databases/example-sql@1",
        SupportStatus.SUPPORTED,
    )
    assert ComponentSpecification.from_dict(specification.to_dict()) == specification
    reordered = spec()
    for name in ("capabilities", "configuration", "aliases"):
        reordered[name] = list(reversed(reordered[name]))
    again = ComponentSpecification.from_dict(reordered)
    assert (again.to_dict(), again.content_hash) == (specification.to_dict(), specification.content_hash)
    changed = ComponentSpecification.from_dict(spec(description="Changed."))
    assert changed.content_hash != specification.content_hash  # a changed claim is a new content
    assert [c.id for c in specification.capabilities] == sorted(c.id for c in specification.capabilities)


def test_every_claim_carries_provenance_and_documented_claims_cite_sources() -> None:
    specification = ComponentSpecification.from_dict(spec())
    claims = dict(specification.claims())
    assert "capabilities.transactions" in claims
    assert "configuration.max_connections" in claims
    assert claims["capabilities.full_text_search"].kind is ProvenanceKind.UNKNOWN
    source_ids = {s.id for s in specification.sources}
    for path, provenance in claims.items():
        if provenance.kind is ProvenanceKind.DOCUMENTED:
            assert provenance.sources, path
            assert set(provenance.sources) <= source_ids, path
    assert refused(spec(sources=[]))["fields"] == ["sources"]


@pytest.mark.parametrize(
    ("provenance", "field"),
    [
        ({"kind": "documented"}, "sources"),  # a documented claim cites its source
        ({"kind": "measured"}, "sources"),  # so does a measurement
        ({"kind": "estimated"}, "model"),  # an estimate states its model or assumptions
        ({"kind": "inferred"}, "assumptions"),  # an inference states its reasoning
        ({"kind": "unknown", "sources": ["docs"]}, "sources"),  # unknown rests on nothing
        ({"kind": "documented", "sources": ["docs"], "model": "m1"}, "model"),  # only estimates have models
        ({"kind": "guessed"}, None),
    ],
)
def test_provenance_kinds_are_never_mixed_up(provenance: dict[str, Any], field: str | None) -> None:
    data = spec(capabilities=[{"id": "transactions", "state": "native", "provenance": provenance}])
    fields = refused(data)["fields"]
    assert fields == ([f"capabilities[0].provenance.{field}"] if field else ["capabilities[0].provenance"])


def test_estimates_and_inferences_are_labelled_not_facts() -> None:
    estimate = Provenance(ProvenanceKind.ESTIMATED, model="linear@1", assumptions=("Uniform keys.",))
    inference = Provenance(ProvenanceKind.INFERRED, assumptions=("Follows from the documented protocol.",))
    assert estimate.is_known
    assert estimate.kind is not ProvenanceKind.DOCUMENTED
    assert Provenance.from_dict(inference.to_dict()) == inference
    assert not Provenance.unknown().is_known


def test_capability_states_are_explicit_and_never_assumed() -> None:
    documented = Provenance(ProvenanceKind.DOCUMENTED, ("docs",))
    assert Capability("transactions", CapabilityState.NATIVE, documented).state is CapabilityState.NATIVE
    for state, provenance in [
        (CapabilityState.UNKNOWN, documented),  # unknown is not documented
        (CapabilityState.NATIVE, Provenance.unknown()),  # a stated state needs evidence
        (CapabilityState.REQUIRES_EXTERNAL, documented),  # names what it needs
        (CapabilityState.REQUIRES_CONFIGURATION, documented),
    ]:
        with pytest.raises(InvalidSpecification):
            Capability("transactions", state, provenance)
    with pytest.raises(InvalidSpecification):
        Capability("teleportation", CapabilityState.NATIVE, documented)  # only the vocabulary
    assert "encryption_at_rest" in CAPABILITIES
    assert "strong_consistency" in CAPABILITIES


def test_configuration_fields_are_architecture_ir_properties() -> None:
    specification = ComponentSpecification.from_dict(spec())
    for configured in specification.configuration:
        assert configured.property in NODE_PROPERTIES
    replicas = next(f for f in specification.configuration if f.property == "replicas")
    assert (replicas.default, replicas.provenance.kind) == (None, ProvenanceKind.UNKNOWN)  # never filled in
    invented = spec(configuration=[{"property": "turbo_mode"}])
    assert refused(invented)["fields"] == ["configuration[0].property"]
    # a property of another kind: CPU limits are not a storage property
    bucket = {k: v for k, v in spec().items() if k not in {"configuration", "capacity", "billing"}}
    bucket |= {"id": "storage/example-bucket", "category": "storage", "node_kinds": ["storage"],
               "configuration": [{"property": "cpu_limit_cores"}]}  # fmt: skip
    assert "configuration.cpu_limit_cores" in refused(bucket)["fields"]


def test_a_default_is_stated_only_when_documented_and_valid() -> None:
    documented = Provenance(ProvenanceKind.DOCUMENTED, ("docs",))
    assert ConfigurationField("max_connections", documented, default=100).default == 100
    with pytest.raises(InvalidSpecification):
        ConfigurationField("max_connections", Provenance.unknown(), default=100)  # an undocumented default
    with pytest.raises(InvalidSpecification):
        ConfigurationField(
            "max_connections", Provenance(ProvenanceKind.INFERRED, assumptions=("x",)), default=1
        )
    with pytest.raises(InvalidSpecification):
        ConfigurationField("max_connections", documented, default=-5)  # the IR's range still holds
    with pytest.raises(InvalidSpecification):
        ConfigurationField("max_connections", documented)  # evidence for a default that is not there
    floats = spec(configuration=[{"property": "cpu_limit_cores", "default": 0.5, "provenance": DOCUMENTED}])
    assert refused(floats)["fields"] == ["configuration[0]"]  # exact numbers only


def test_capacity_values_carry_their_basis_and_no_generic_throughput() -> None:
    documented = Provenance(ProvenanceKind.DOCUMENTED, ("docs",))
    dimension = CapacityDimension("item_size", "KB", Scope.ITEM, documented, value=Decimal(400))
    assert dimension.value == Decimal(400)
    inferred = Provenance(ProvenanceKind.INFERRED, assumptions=("It is fast.",))
    with pytest.raises(InvalidSpecification):  # an inferred value is not a capacity
        CapacityDimension("reads", "operations/second", Scope.INSTANCE, inferred, value=Decimal(10_000))
    with pytest.raises(InvalidSpecification):  # an estimate states the workload it holds for
        CapacityDimension(
            "reads", "operations/second", Scope.INSTANCE, Provenance(ProvenanceKind.ESTIMATED, model="m@1"),
            value=Decimal(10),
        )  # fmt: skip
    with pytest.raises(InvalidSpecification):
        CapacityDimension("depth", "parsecs", Scope.QUEUE, documented)  # capacity units only
    configured = CapacityDimension(
        "connections", "connections", Scope.INSTANCE, Provenance.unknown(), property="max_connections"
    )
    assert configured.value is None  # read from the architecture's configuration, not assumed


def test_support_states_say_exactly_what_is_claimed() -> None:
    assert refused(spec(support_status="planned"))["fields"] == ["support_status"]  # planned claims nothing
    sections = {"capabilities", "configuration", "capacity", "scaling", "failure_modes", "signals",
                "security", "billing", "operations"}  # fmt: skip
    empty = {k: v for k, v in spec().items() if k not in sections}
    assert ComponentSpecification.from_dict(dict(empty, support_status="planned")).capabilities == ()
    assert refused(dict(empty, support_status="partial"))["fields"] == ["capabilities"]
    supported = refused(dict(empty, support_status="supported"))["fields"]
    assert supported == ["capabilities", "configuration", "failure_modes", "security", "signals"]
    deprecated = ComponentSpecification.from_dict(
        spec(support_status="deprecated", replaced_by="databases/next")
    )
    assert deprecated.replaced_by == "databases/next"
    assert refused(spec(replaced_by="databases/next"))["fields"] == ["replaced_by"]  # only when deprecated


@pytest.mark.parametrize(
    ("overrides", "fields"),
    [
        ({"id": "db/example-sql"}, ["id"]),  # the category's directory
        ({"id": "databases/a/b"}, ["id"]),
        ({"category": "quantum"}, ["category", "node_kinds"]),
        ({"node_kinds": ["queue"]}, ["node_kinds"]),  # a database is not modeled as a queue
        ({"node_kinds": []}, ["node_kinds"]),
        ({"technology": "Example SQL"}, ["technology"]),  # the IR's technology identifier
        ({"version": 0}, ["version"]),
        ({"version": True}, ["version"]),
    ],
)
def test_identity_is_checked(overrides: dict[str, Any], fields: list[str]) -> None:
    assert refused(spec(**overrides))["fields"] == fields


def test_untrusted_input_is_refused_with_the_fields_at_fault() -> None:
    assert refused(spec(price_per_hour="0.10")) == {
        "component": "databases/example-sql",
        "fields": ["price_per_hour"],
    }
    assert refused([1, 2]) == {"component": None, "fields": [""]}
    missing = spec()
    del missing["description"]
    assert refused(missing)["fields"] == ["description"]
    typo = copy.deepcopy(spec())
    typo["signals"][0]["colection"] = "x"
    assert refused(typo)["fields"] == ["signals[0].colection"]
    unknown_signal = copy.deepcopy(spec())
    unknown_signal["failure_modes"][0]["signals"] = ["cpu"]
    assert refused(unknown_signal)["fields"] == ["failure_modes.connection_exhaustion.signals"]
    duplicate = spec(capabilities=[spec()["capabilities"][0]] * 2)
    assert refused(duplicate)["fields"] == ["capabilities"]
    priced = spec(billing=[{"id": "x", "unit": "dollars", "provenance": DOCUMENTED}])
    assert refused(priced)["fields"] == ["billing[0]"]
    assert refused(spec(aliases=[f"a{i}" for i in range(101)]))["fields"] == ["aliases"]


def test_security_properties_name_the_ir_property_that_enables_them() -> None:
    [transit] = ComponentSpecification.from_dict(spec()).security
    assert (transit.property, transit.state) == (
        "tls",
        CapabilityState.REQUIRES_CONFIGURATION,
    )  # a connection's
    invented = copy.deepcopy(spec())
    invented["security"][0]["property"] = "military_grade"
    assert refused(invented)["fields"] == ["security[0].property"]


def test_the_categories_are_a_registry_of_ir_node_kinds() -> None:
    assert set(CATEGORIES) == {"compute", "database", "messaging", "storage", "networking", "observability"}
    directories = [c.directory for c in CATEGORIES.values()]
    assert len(directories) == len(set(directories))
    for category in CATEGORIES.values():
        assert category.node_kinds
        assert category.node_kinds <= set(NodeKind)
