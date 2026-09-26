"""The project's architecture policy: the value object, the entity and the use case."""

from typing import Any

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.errors import InvalidArchitecturePolicy, ProjectArchived, ProjectNotFound
from core.domain.projects.policies import MAX_POLICY_ENTRIES, ArchitecturePolicy
from core.domain.projects.project_service import ProjectService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

STRICT = ArchitecturePolicy(
    allowed_technologies=frozenset({"postgresql", "fastapi"}),
    prohibited_technologies=frozenset({"mongodb"}),
    allowed_regions=frozenset({"eu-west-1"}),
    require_tls=True,
    max_components=20,
)


def test_the_default_policy_constrains_nothing() -> None:
    assert ArchitecturePolicy().is_empty
    assert ArchitecturePolicy.from_dict({}) == ArchitecturePolicy()
    assert not STRICT.is_empty


def test_names_are_normalized_and_the_dict_is_canonical() -> None:
    policy = ArchitecturePolicy.from_dict(
        {"allowed_technologies": [" PostgreSQL ", "fastapi", "postgresql"], "allowed_regions": ["EU-WEST-1"]}
    )
    assert policy.allowed_technologies == frozenset({"postgresql", "fastapi"})
    assert policy.to_dict() == {
        "allowed_technologies": ["fastapi", "postgresql"],
        "prohibited_technologies": [],
        "allowed_regions": ["eu-west-1"],
        "require_tls": False,
        "max_components": None,
    }
    assert ArchitecturePolicy.from_dict(STRICT.to_dict()) == STRICT


@pytest.mark.parametrize(
    ("raw", "field", "reason"),
    [
        ({"colour": "red"}, "colour", "unknown"),
        ({"allowed_technologies": "postgresql"}, "allowed_technologies", "not_a_list"),
        ({"allowed_technologies": ["Postgre SQL"]}, "allowed_technologies", "invalid_value"),
        ({"allowed_technologies": [3]}, "allowed_technologies", "invalid_value"),
        (
            {"prohibited_technologies": ["x"] * (MAX_POLICY_ENTRIES + 1)},
            "prohibited_technologies",
            "too_many",
        ),
        ({"allowed_regions": ["eu west"]}, "allowed_regions", "invalid_value"),
        (
            {"allowed_technologies": ["redis"], "prohibited_technologies": ["redis"]},
            "prohibited_technologies",
            "also_allowed",
        ),
        ({"require_tls": "yes"}, "require_tls", "not_a_boolean"),
        ({"max_components": 0}, "max_components", "out_of_range"),
        ({"max_components": 1001}, "max_components", "out_of_range"),
        ({"max_components": True}, "max_components", "out_of_range"),
    ],
)
def test_invalid_policies_are_refused(raw: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidArchitecturePolicy) as raised:
        ArchitecturePolicy.from_dict(raw)
    assert raised.value.details == {"field": field, "reason": reason}


# --- the use case --------------------------------------------------------------------------------


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


async def verified(uow: FakeUnitOfWork, clock: FakeClock, email: str) -> User:
    user = await uow.users.add(NewUser(email, email[:4], "$argon2id$x"))
    await uow.users.mark_email_verified(user.id, clock.now)
    stored = await uow.users.get(user.id)
    assert stored is not None
    return stored


async def test_admins_set_the_policy_and_it_is_audited_by_field(
    uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    projects = ProjectService(uow, clock=clock)
    ada = await verified(uow, clock, "ada@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    project = await projects.create(membership=acme.membership, name="Orders")
    assert project.policy.is_empty

    access = await projects.update_policy(project_id=project.id, user_id=ada.id, policy=STRICT)

    assert access.project.policy == STRICT
    stored = await uow.projects.get_live(project.id)
    assert stored is not None
    assert stored.policy == STRICT
    event = uow.audit.events[-1]
    assert (event.action.value, event.resource_id) == ("project.policy_updated", project.id)
    assert event.metadata == {"fields": sorted(STRICT.to_dict())}  # names only, never values

    recorded = len(uow.audit.events)
    await projects.update_policy(project_id=project.id, user_id=ada.id, policy=STRICT)
    assert len(uow.audit.events) == recorded  # unchanged: nothing saved or recorded


async def test_members_viewers_and_other_tenants_cannot_set_it(uow: FakeUnitOfWork, clock: FakeClock) -> None:
    projects = ProjectService(uow, clock=clock)
    ada = await verified(uow, clock, "ada@example.com")
    mel = await verified(uow, clock, "mel@example.com")
    eve = await verified(uow, clock, "eve@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    await uow.memberships.add(organization_id=acme.organization.id, user_id=mel.id, role=Role.MEMBER)
    project = await projects.create(membership=acme.membership, name="Orders")

    with pytest.raises(PermissionDenied):
        await projects.update_policy(project_id=project.id, user_id=mel.id, policy=STRICT)
    with pytest.raises(ProjectNotFound):
        await projects.update_policy(project_id=project.id, user_id=eve.id, policy=STRICT)
    stored = await uow.projects.get_live(project.id)
    assert stored is not None
    assert stored.policy.is_empty


async def test_an_archived_projects_policy_is_read_only(uow: FakeUnitOfWork, clock: FakeClock) -> None:
    projects = ProjectService(uow, clock=clock)
    ada = await verified(uow, clock, "ada@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    project = await projects.create(membership=acme.membership, name="Orders")
    await uow.projects.save(project.archive(clock.now))

    with pytest.raises(ProjectArchived):
        await projects.update_policy(project_id=project.id, user_id=ada.id, policy=STRICT)
