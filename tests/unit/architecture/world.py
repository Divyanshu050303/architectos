"""A small world for architecture tests: three people, two organizations, two projects."""

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.entities import Project
from core.domain.projects.project_service import ProjectService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


class World:
    """ada owns Acme (projects ``project`` and ``other``), vic is a viewer there, eve owns Globex."""

    def __init__(self, ada: User, vic: User, eve: User, project: Project, other: Project) -> None:
        self.ada, self.vic, self.eve, self.project, self.other = ada, vic, eve, project, other


async def make_world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    users = []
    for email in ("ada@example.com", "vic@example.com", "eve@example.com"):
        user = await uow.users.add(NewUser(email, email[:3], "$argon2id$x"))
        await uow.users.mark_email_verified(user.id, clock.now)
        stored = await uow.users.get(user.id)
        assert stored is not None
        users.append(stored)
    ada, vic, eve = users
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    await OrganizationService(uow, clock=clock).create(user=eve, name="Globex")
    await uow.memberships.add(organization_id=acme.organization.id, user_id=vic.id, role=Role.VIEWER)
    projects = ProjectService(uow, clock=clock)
    project = await projects.create(membership=acme.membership, name="Food Delivery")
    other = await projects.create(membership=acme.membership, name="Payments")
    return World(ada, vic, eve, project, other)
