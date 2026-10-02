"""The migration pattern registry (Migration Planning, phase 3): deterministic, versioned patterns that
declare their contract; strategies supported only when the architecture declares their
prerequisites (with what is missing otherwise); unsupported strategies answered, never fabricated;
alternatives side by side, the plan's strategy chosen by the request or the documented default —
never by a score."""

import dataclasses
import json
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.components.capabilities import CAPABILITIES
from core.domain.migrations.entities import MigrationRequest, TargetSpec
from core.domain.migrations.plans import StrategyOption
from core.domain.migrations.values import DowntimeStatus, FindingType
from engines.migration.changes import analyze, revision_target
from engines.migration.patternbook import default_registry, stateful_replacements
from engines.migration.patterns import (
    DEFAULT_STRATEGY,
    DuplicatePattern,
    PatternKind,
    PlanningContext,
    Registry,
    choose,
    strategy_options,
)
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.migration.test_migration_changes import (
    ARCHITECTURE,
    SOURCE,
    SOURCE_IR,
    changed,
    original,
    shop,
)

REGISTRY = default_registry()


def context(
    after: ArchitectureIR, source_ir: ArchitectureIR = SOURCE_IR, strategy: str | None = None
) -> PlanningContext:
    source = (
        SOURCE
        if source_ir is SOURCE_IR
        else dataclasses.replace(SOURCE, content_hash=content_hash(source_ir))
    )
    analysis = analyze(source_ir, after, source, revision_target(source, 2, content_hash(after)))
    request = MigrationRequest(ARCHITECTURE, 1, TargetSpec(revision=2), strategy=strategy)
    return PlanningContext(source_ir, after, analysis, request)


def options(after: ArchitectureIR, source_ir: ArchitectureIR = SOURCE_IR) -> dict[str, StrategyOption]:
    return {o.pattern.split("@")[0]: o for o in strategy_options(context(after, source_ir), REGISTRY)}


def test_every_pattern_is_versioned_and_declares_its_contract() -> None:
    patterns = REGISTRY.patterns()
    templates = {p.meta.id for p in REGISTRY.patterns(PatternKind.TEMPLATE)}
    strategies = {p.meta.id for p in REGISTRY.patterns(PatternKind.STRATEGY)}
    assert templates == {"reconfigure", "provision", "decommission", "reroute"}
    assert strategies == {
        "in_place", "rolling", "blue_green", "replication_cutover", "canary", "expand_contract", "strangler",
    }  # fmt: skip
    for pattern in patterns:
        meta = pattern.meta
        assert meta.version >= 1
        assert set(meta.capabilities) <= set(CAPABILITIES)
        if meta.supported:
            assert meta.steps
            assert meta.rollback
        else:
            assert meta.unsupported  # says why it is not planned
    assert REGISTRY.versions()["rolling"] == 1
    with pytest.raises(DuplicatePattern):
        Registry([*patterns, patterns[0]])


def test_in_place_is_the_documented_default() -> None:
    found = options(shop(api=changed("api", replicas=4)))
    assert found["in_place"].supported
    chosen, findings = choose(tuple(found.values()), None)
    assert chosen is not None
    assert chosen.pattern == f"{DEFAULT_STRATEGY}@1"
    assert findings == ()


def test_rolling_needs_declared_replicas_and_a_health_check() -> None:
    missing = options(shop(api=changed("api", replicas=4)))["rolling"]
    assert (missing.applies, missing.supported) == (True, False)
    assert missing.missing == ("health_check of api declared true in the target.",)
    assert missing.downtime is DowntimeStatus.UNKNOWN  # not claimed online without its prerequisites
    single = shop(api=changed("api", replicas=1))
    one = options(shop(api=changed("api", replicas=1, health_check=True)), source_ir=single)["rolling"]
    assert "replicas of api: at least 2 declared in the source and the target." in one.missing
    ready = options(shop(api=changed("api", replicas=4, health_check=True)))["rolling"]
    assert (ready.supported, ready.downtime, ready.subjects) == (
        True,
        DowntimeStatus.MODELED_ONLINE,
        ("api",),
    )


def test_blue_green_needs_a_routing_component_in_front() -> None:
    assert options(shop(api=changed("api", replicas=4)))["blue_green"].missing == (
        "A load balancer or gateway in front of api in the target, to switch traffic between the two "
        "environments.",
    )
    routed = dataclasses.replace(
        shop(api=changed("api", replicas=4), lb=node("lb", NodeKind.LOAD_BALANCER)),
        connections=(
            connection("web-lb", "web", "lb", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("lb-api", "lb", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )
    assert options(routed)["blue_green"].supported


def mysql(**values: Any) -> ArchitectureIR:
    db = original("db")
    configured = Configuration({**db.configuration.values, **values})
    return shop(db=dataclasses.replace(db, technology=Technology("mysql", "8"), configuration=configured))


def test_replication_cutover_needs_a_declared_replication_mode() -> None:
    without = options(mysql())["replication_cutover"]
    assert (without.applies, without.supported) == (True, False)
    assert without.missing[0].startswith("replication_mode of db in the target")
    declared = options(mysql(replication_mode="asynchronous"))["replication_cutover"]
    assert (declared.supported, declared.downtime) == (True, DowntimeStatus.POTENTIAL_DOWNTIME)
    assert not options(shop(api=changed("api", replicas=4)))["replication_cutover"].applies


def test_a_stateful_replacement_is_one_removed_and_one_added_of_the_same_kind() -> None:
    nodes = tuple(n for n in SOURCE_IR.nodes if n.id != "db")
    new_db = node("db2", NodeKind.DATABASE, configuration=Configuration({"replication_mode": "synchronous"}))
    replaced = ArchitectureIR(
        "Shop",
        nodes=(*nodes, new_db),
        connections=(
            *(c for c in SOURCE_IR.connections if c.id == "web-api"),
            connection("api-db2", "api", "db2", protocol="mysql"),
        ),
    )
    assert stateful_replacements(context(replaced)) == (("db", "db2"),)
    ambiguous = dataclasses.replace(replaced, nodes=(*replaced.nodes, node("db3", NodeKind.DATABASE)))
    assert stateful_replacements(context(ambiguous)) == ()  # two candidates: a person pairs them


def test_templates_cover_their_changes_and_never_a_stateful_technology_change() -> None:
    reconfigure = REGISTRY.get("reconfigure")
    assert reconfigure is not None
    assert not reconfigure.assess(context(mysql(replication_mode="asynchronous"))).applies  # its data moves
    ctx = context(shop(api=changed("api", replicas=4), cache=node("cache", NodeKind.CACHE)))
    assessed = {p.meta.id: p.assess(ctx) for p in REGISTRY.patterns(PatternKind.TEMPLATE)}
    assert assessed["reconfigure"].subjects == ("api",)
    assert assessed["provision"].subjects == ("cache",)
    assert not assessed["decommission"].applies


@pytest.mark.parametrize(
    ("preferred", "expected"),
    [
        ("canary", "Traffic weights and the share of requests per version are not modeled."),
        ("expand_contract", "Data schemas and their versions are not modeled."),
        ("rolling", "health_check of api declared true in the target."),
        ("teleport", "not known"),
    ],
)
def test_a_preferred_strategy_that_is_not_supported_is_answered(preferred: str, expected: str) -> None:
    found = strategy_options(context(shop(api=changed("api", replicas=4)), strategy=preferred), REGISTRY)
    chosen, [finding] = choose(found, preferred)
    assert finding.type is FindingType.STRATEGY_NOT_SUPPORTED
    assert expected in finding.message or expected in finding.missing
    assert chosen is not None
    assert chosen.pattern == "in_place@1"  # the documented default, with the finding stated


def test_a_supported_preference_is_followed() -> None:
    found = strategy_options(context(shop(api=changed("api", replicas=4, health_check=True))), REGISTRY)
    chosen, findings = choose(found, "rolling")
    assert chosen is not None
    assert (chosen.pattern, findings) == ("rolling@1", ())


def test_alternatives_are_deterministic_and_unscored() -> None:
    after = shop(api=changed("api", replicas=4, health_check=True))
    first = [o.to_dict() for o in strategy_options(context(after), REGISTRY)]
    assert first == [o.to_dict() for o in strategy_options(context(after), default_registry())]
    text = json.dumps(first)
    assert not any(f'"{word}' in text for word in ("score", "rank", "best", "recommended", "probability"))
    assert [o["pattern"] for o in first] == sorted(o["pattern"] for o in first)
