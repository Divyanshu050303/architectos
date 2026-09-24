from dataclasses import replace

from fastapi import APIRouter, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.permissions import CurrentMembership, require_permission
from apps.api.dependencies.services import OrganizationServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.organization import (
    CreateOrganizationRequest,
    OrganizationList,
    OrganizationResponse,
    UpdateOrganizationRequest,
)
from core.domain.organizations.permissions import Permission

router = APIRouter(prefix="/organizations", tags=["organizations"])

SCOPED_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "organization_not_found (also when you are not a member)"},
}


@router.get("", response_model=OrganizationList, summary="Organizations you belong to")
async def list_organizations(current: CurrentUser, organizations: OrganizationServiceDep) -> OrganizationList:
    found = await organizations.list_for_user(user_id=current.user.id)
    return OrganizationList(organizations=[OrganizationResponse.from_domain(o) for o in found])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse, "description": "email_not_verified"},
        422: {"model": ErrorResponse, "description": "invalid_organization_name"},
    },
    summary="Create an organization",
    description="You become its owner. Requires a verified email address.",
)
async def create_organization(
    body: CreateOrganizationRequest, current: CurrentUser, organizations: OrganizationServiceDep
) -> OrganizationResponse:
    created = await organizations.create(user=current.user, name=body.name)
    return OrganizationResponse.from_domain(created)


@router.get(
    "/{organization_id}",
    response_model=OrganizationResponse,
    responses=SCOPED_ERRORS,
    dependencies=[require_permission(Permission.ORGANIZATION_READ)],
    summary="An organization you belong to",
)
async def get_organization(scoped: CurrentMembership) -> OrganizationResponse:
    return OrganizationResponse.from_domain(scoped)


@router.patch(
    "/{organization_id}",
    response_model=OrganizationResponse,
    responses=SCOPED_ERRORS | {422: {"model": ErrorResponse, "description": "invalid_organization_name"}},
    dependencies=[require_permission(Permission.ORGANIZATION_UPDATE)],
    summary="Rename an organization",
    description="Owners and admins.",
)
async def update_organization(
    body: UpdateOrganizationRequest, scoped: CurrentMembership, organizations: OrganizationServiceDep
) -> OrganizationResponse:
    renamed = await organizations.rename(membership=scoped.membership, name=body.name)
    return OrganizationResponse.from_domain(replace(scoped, organization=renamed))


@router.delete(
    "/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=SCOPED_ERRORS,
    dependencies=[require_permission(Permission.ORGANIZATION_DELETE)],
    summary="Delete an organization",
    description="Owners only. The organization disappears for every member; its history is retained.",
)
async def delete_organization(scoped: CurrentMembership, organizations: OrganizationServiceDep) -> None:
    await organizations.delete(membership=scoped.membership)
