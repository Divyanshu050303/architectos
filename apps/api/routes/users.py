from fastapi import APIRouter

from apps.api.dependencies.auth import CurrentUser
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.users import UserResponse

router = APIRouter(prefix="/me", tags=["users"])


@router.get(
    "",
    response_model=UserResponse,
    responses={401: {"model": ErrorResponse}},
    summary="The signed-in user",
)
async def get_me(current: CurrentUser) -> UserResponse:
    return UserResponse.from_user(current.user)
