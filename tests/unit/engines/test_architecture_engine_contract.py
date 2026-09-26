"""The Architecture Engine boundary (Architecture IR phase 5): a generator consumes the planning
input of a requirement set and produces the canonical IR, which a person then adopts as a revision.
The generator here is a stand-in; the contract is what is tested."""

import dataclasses
import uuid
from decimal import Decimal

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.traceability import RequirementRef
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.versions import RevisionSource
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.planning import PlanningInputV2
from core.domain.requirements.requirement_service import RequirementService
from core.domain.requirements.requirement_set_service import RequirementSetService
from engines.architecture.service import ArchitectureGenerator, ArchitectureProposal, check_proposal
from tests.unit.architecture.world import World, make_world
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


DEFAULT = Provenance(ProvenanceSource.SYSTEM_DEFAULT, inferred=True, actor="engine:stand-in")


class StandInGenerator:
    """One service sized from the throughput requirement: the smallest thing that honours the contract."""

    async def propose(
        self, planning_input: PlanningInputV2, *, requirement_set_id: uuid.UUID | None
    ) -> ArchitectureProposal:
        refs = tuple(RequirementRef(uuid.UUID(r["id"]), r["version"]) for r in planning_input["requirements"])
        service = Node(
            "api",
            NodeKind.SERVICE,
            "API",
            configuration=Configuration({"replicas": 2, "cpu_request_cores": Decimal("0.5")}),
            requirement_refs=refs,
            field_provenance={"configuration.replicas": DEFAULT},
        )
        ir = ArchitectureIR("Proposed", nodes=(service,), requirement_refs=refs, provenance=DEFAULT)
        return ArchitectureProposal(
            ir, "stand-in-0.1", "One service meets the stated load.", requirement_set_id
        )


async def planning_input(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> tuple[uuid.UUID, PlanningInputV2]:
    await RequirementService(uow, clock=clock).create(
        project_id=world.project.id,
        user_id=world.ada.id,
        type=RequirementType.CAPACITY,
        category="throughput",
        title="API throughput",
        statement="The API must support 2,000 requests per second.",
        priority=RequirementPriority.CRITICAL,
        status=RequirementStatus.ACTIVE,
        structured_data={
            "metric": "requests_per_second",
            "operator": ">=",
            "value": 2000,
            "unit": "requests/second",
        },
    )
    sets = RequirementSetService(uow, clock=clock)
    created = await sets.create(project_id=world.project.id, user_id=world.ada.id)
    _, document = await sets.planning_input(
        project_id=world.project.id, set_id=created.id, user_id=world.ada.id
    )
    return created.id, document  # type: ignore[return-value]


async def test_a_proposal_becomes_a_revision_traceable_to_its_requirement_set(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    set_id, document = await planning_input(uow, clock, world)
    generator: ArchitectureGenerator = StandInGenerator()
    proposal = await generator.propose(document, requirement_set_id=set_id)
    assert check_proposal(proposal, document) == ()

    architecture, revision = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id,
        user_id=world.ada.id,
        name="Proposed architecture",
        ir=proposal.ir,
        source=RevisionSource.SYSTEM,
        reason=proposal.rationale,
        requirement_set_id=proposal.requirement_set_id,
    )
    assert (revision.source, revision.requirement_set_id) == (RevisionSource.SYSTEM, set_id)
    [ref] = revision.ir.requirement_refs
    assert ref.requirement_id == uuid.UUID(document["requirements"][0]["id"])
    assert architecture.current_revision == 1


@pytest.mark.parametrize(
    ("tamper", "rule"),
    [
        (lambda ir: dataclasses.replace(ir, provenance=None), "missing_provenance"),
        (
            lambda ir: dataclasses.replace(
                ir, provenance=Provenance(ProvenanceSource.TERRAFORM, verified=True)
            ),
            "not_generated",
        ),
        (
            lambda ir: dataclasses.replace(
                ir,
                nodes=tuple(
                    dataclasses.replace(
                        n,
                        field_provenance={
                            "configuration.replicas": Provenance(ProvenanceSource.USER_EDIT, verified=True)
                        },
                    )
                    for n in ir.nodes
                ),
            ),
            "claims_verified",
        ),
        (
            lambda ir: dataclasses.replace(ir, requirement_refs=(RequirementRef(uuid.uuid4()),)),
            "requirement_not_given",
        ),
    ],
    ids=["no-provenance", "claims-discovery", "claims-verified", "foreign-requirement"],
)
async def test_what_a_generator_may_not_produce(
    uow: FakeUnitOfWork, clock: FakeClock, world: World, tamper: object, rule: str
) -> None:
    set_id, document = await planning_input(uow, clock, world)
    proposal = await StandInGenerator().propose(document, requirement_set_id=set_id)
    bad = dataclasses.replace(proposal, ir=tamper(proposal.ir))  # type: ignore[operator]
    assert rule in {v.rule for v in check_proposal(bad, document)}
