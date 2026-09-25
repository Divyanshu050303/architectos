import uuid

from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.errors import ProjectNotFound
from core.domain.projects.repository import ProjectLock
from core.domain.unit_of_work import UnitOfWork


async def project_access(
    uow: UnitOfWork,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: Permission,
    *,
    lock: ProjectLock | None = None,
) -> ProjectAccess:
    """The project with the caller's membership, inside the caller's transaction; 404 for anyone
    outside its organization, 403 without ``permission``. Every write under a project passes a
    ``lock`` (see ProjectLock) and requires the project to be modifiable: archived projects are
    frozen, and archiving waits for writes in progress."""
    access = await uow.projects.get_for_member(project_id, user_id=user_id, lock=lock)
    if access is None:
        raise ProjectNotFound
    access.membership.require(permission)
    if lock is not None:
        access.project.ensure_modifiable()
    return access
