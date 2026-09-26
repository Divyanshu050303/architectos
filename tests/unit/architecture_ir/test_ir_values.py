"""Identifiers, provenance, technologies and references (Architecture IR phase 1)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.architecture_ir.component import Technology, component_problems
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.traceability import Assumption, DecisionRef, RequirementRef
from core.architecture_ir.values import id_problems, number_problems

from .builders import LATENCY, THROUGHPUT, llm, rules

S = ProvenanceSource


@pytest.mark.parametrize(
    "value", ["api", "API-2", "aws_db_instance.main", "default/deployment/api", "urn:svc:1", "a" * 128]
)
def test_valid_ids(value: str) -> None:
    assert id_problems(value, "id") == []


@pytest.mark.parametrize(
    "value", ["", " api", "-api", "my api", "a" * 129, "api\n", "ap" + chr(0x202E) + "i", 7, None]
)
def test_invalid_ids(value: object) -> None:
    [problem] = id_problems(value, "id")
    assert (problem.rule, problem.field) == ("invalid_id", "id")


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        (0.1, "not_a_number"),  # floats are never exact
        (True, "not_a_number"),
        ("3", "not_a_number"),
        (Decimal("NaN"), "not_a_number"),
        (Decimal("1E+15"), "out_of_range"),
        (Decimal("0.0000000001"), "too_precise"),
    ],
)
def test_numbers_are_exact(value: object, rule: str) -> None:
    assert [p.rule for p in number_problems(value, "x")] == [rule]
    assert number_problems(Decimal("0.5"), "x") == number_problems(3, "x") == []


# --- provenance ------------------------------------------------------------------------------------


def test_provenance_records_where_a_value_came_from() -> None:
    at = datetime(2026, 9, 26, tzinfo=UTC)
    found = Provenance(
        S.TERRAFORM, "  aws_db_instance.main ", verified=True, actor="discovery:tf", recorded_at=at
    )
    assert (found.reference, found.discovered, found.verified) == ("aws_db_instance.main", True, True)
    assert Provenance(S.USER_INPUT).discovered is False


def test_a_model_proposal_states_its_confidence_and_is_never_verified_by_itself() -> None:
    assert rules(lambda: Provenance(S.LLM_PROPOSAL)) == {"required"}
    assert llm("1").confidence == Decimal(1)  # full confidence is still not verification
    assert rules(lambda: Provenance(S.LLM_PROPOSAL, confidence=Decimal("0.9"), verified=True)) == {
        "unverifiable"
    }
    assert rules(lambda: Provenance(S.SYSTEM_DEFAULT, verified=True)) == {"unverifiable"}


@pytest.mark.parametrize(
    ("kwargs", "rule"),
    [
        ({"confidence": Decimal("1.1")}, "out_of_range"),
        ({"confidence": Decimal("0.1234")}, "too_precise"),
        ({"confidence": 0.5}, "not_a_number"),
        ({"verified": True, "inferred": True}, "contradictory"),
        ({"recorded_at": datetime(2026, 9, 26)}, "invalid_timestamp"),  # noqa: DTZ001 - the point of the test
        ({"reference": "x" * 501}, "too_long"),
        ({"actor": "a\x00b"}, "control_characters"),
    ],
)
def test_invalid_provenance(kwargs: dict[str, object], rule: str) -> None:
    assert rules(lambda: Provenance(S.CLOUD_DISCOVERY, **kwargs)) == {rule}  # type: ignore[arg-type]


# --- technology and catalog references -------------------------------------------------------------


def test_technologies_are_identifiers_not_a_closed_list() -> None:
    assert Technology(" PostgreSQL ", "16") == Technology("postgresql", "16")
    assert Technology("some-new-db").name == "some-new-db"  # nothing to register
    assert rules(lambda: Technology("PostgreSQL 16")) == {"invalid_technology"}
    assert rules(lambda: Technology("redis", "7 stable")) == {"invalid_technology"}


def test_component_references_are_catalog_paths() -> None:
    assert component_problems("databases/postgresql") == component_problems(None) == []
    assert [p.rule for p in component_problems("Databases/PostgreSQL")] == ["invalid_component_reference"]


# --- traceability ----------------------------------------------------------------------------------


def test_requirement_references() -> None:
    assert RequirementRef(LATENCY, 3).version == 3
    assert rules(lambda: RequirementRef(LATENCY, 0)) == {"invalid_reference"}
    assert rules(lambda: RequirementRef("REQ-1")) == {"invalid_reference"}  # type: ignore[arg-type]


def test_assumptions_say_who_assumed_them() -> None:
    assumption = Assumption(
        "peak",
        "  Peak traffic is 3x the daily average. ",
        llm(),
        subject_ids=("api", "api", "web"),
        requirement_refs=(RequirementRef(THROUGHPUT), RequirementRef(LATENCY), RequirementRef(THROUGHPUT)),
    )
    assert assumption.statement == "Peak traffic is 3x the daily average."
    assert assumption.subject_ids == ("api", "web")
    assert assumption.requirement_refs == (RequirementRef(LATENCY), RequirementRef(THROUGHPUT))
    assert rules(lambda: Assumption("x", "", llm())) == {"required"}
    assert rules(lambda: Assumption("x", "Something", None)) == {"required"}  # type: ignore[arg-type]


def test_decision_references_point_at_elements() -> None:
    decision = DecisionRef(uuid.uuid4(), ("db", "api"))
    assert decision.subject_ids == ("api", "db")
    assert rules(lambda: DecisionRef(uuid.uuid4(), ("not an id",))) == {"invalid_id"}
