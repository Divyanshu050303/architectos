import uuid

from fastapi import APIRouter, Response, status

from apps.api.cookies import clear_refresh_cookie
from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import AppSettings, SessionServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.users import SessionItem, SessionList, UserResponse

router = APIRouter(prefix="/me", tags=["users"])


@router.get(
    "",
    response_model=UserResponse,
    responses={401: {"model": ErrorResponse}},
    summary="The signed-in user",
)
async def get_me(current: CurrentUser) -> UserResponse:
    return UserResponse.from_user(current.user)


@router.get(
    "/sessions",
    response_model=SessionList,
    responses={401: {"model": ErrorResponse}},
    summary="Active sessions of the signed-in user",
    description="Unrevoked, unexpired sessions, most recently used first (at most 100).",
)
async def list_sessions(current: CurrentUser, sessions: SessionServiceDep) -> SessionList:
    active = await sessions.list_sessions(user_id=current.user.id)
    return SessionList(
        sessions=[SessionItem.from_session(s, current_session_id=current.session.id) for s in active]
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "session_not_found"},
    },
    summary="Sign out one of your sessions",
    description=(
        "Revokes it immediately: its refresh token and access tokens stop working. "
        "Revoking the current session also clears the refresh cookie."
    ),
)
async def revoke_session(
    session_id: uuid.UUID,
    current: CurrentUser,
    sessions: SessionServiceDep,
    settings: AppSettings,
    response: Response,
) -> None:
    await sessions.revoke_session(user_id=current.user.id, session_id=session_id)
    if session_id == current.session.id:
        clear_refresh_cookie(response, settings)
