from typing import Annotated

from fastapi import APIRouter, Path

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import InvitationServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.organization import OrganizationResponse

router = APIRouter(prefix="/invitations", tags=["organizations"])

TokenPath = Annotated[
    str, Path(min_length=1, max_length=256, description="The token from the invitation email.")
]


@router.post(
    "/{invitation_token}/accept",
    response_model=OrganizationResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse, "description": "email_not_verified, invitation_email_mismatch"},
        404: {"model": ErrorResponse, "description": "invalid_invitation"},
        409: {"model": ErrorResponse, "description": "already_member"},
        410: {"model": ErrorResponse, "description": "invitation_expired"},
    },
    summary="Accept an invitation",
    description=(
        "Signed in, with a verified email address matching the invitation. Joins the organization "
        "with the invited role."
    ),
)
async def accept_invitation(
    invitation_token: TokenPath,
    current: CurrentUser,
    invitations: InvitationServiceDep,
    limits: RateLimitsDep,
) -> OrganizationResponse:
    await limits.enforce("accept_invitation")
    joined = await invitations.accept(user=current.user, token=invitation_token)
    return OrganizationResponse.from_domain(joined)
