import uuid

from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.errors import ProjectNotFound
from core.domain.unit_of_work import UnitOfWork


async def project_access(
    uow: UnitOfWork, project_id: uuid.UUID, user_id: uuid.UUID, permission: Permission, *, lock: bool = False
) -> ProjectAccess:
    """The project with the caller's membership, inside the caller's transaction; 404 for anyone
    outside its organization, 403 without ``permission``. With ``lock`` (every write under a
    project) the project row is locked and must be modifiable: archived projects are frozen."""
    access = await uow.projects.get_for_member(project_id, user_id=user_id, for_update=lock)
    if access is None:
        raise ProjectNotFound
    access.membership.require(permission)
    if lock:
        access.project.ensure_modifiable()
    return access
