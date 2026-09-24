import uuid

from fastapi import APIRouter, Response, status

from apps.api.cookies import clear_refresh_cookie
from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import AppSettings, PasswordServiceDep, SessionServiceDep, UserServiceDep
from apps.api.schemas.auth import ChangePasswordRequest
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.users import (
    DeleteAccountRequest,
    SessionItem,
    SessionList,
    UpdateProfileRequest,
    UserResponse,
)

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


@router.patch(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "incorrect_password"},
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse, "description": "weak_password"},
    },
    summary="Change your password",
    description=(
        "Requires the current password. This session stays signed in (its refresh token is unchanged); "
        "every other session is signed out."
    ),
)
async def change_password(
    body: ChangePasswordRequest, current: CurrentUser, passwords: PasswordServiceDep
) -> None:
    await passwords.change(
        user_id=current.user.id,
        current_session_id=current.session.id,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )


@router.patch(
    "",
    response_model=UserResponse,
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse, "description": "invalid_name, invalid_avatar_url, nothing_to_update"},
    },
    summary="Update your profile",
    description=(
        "Name and avatar only. Email changes need a dedicated verified workflow and are rejected here."
    ),
)
async def update_profile(
    body: UpdateProfileRequest, current: CurrentUser, users: UserServiceDep
) -> UserResponse:
    user = await users.update_profile(
        user_id=current.user.id,
        name=body.name,
        avatar_url=body.avatar_url,
        set_avatar="avatar_url" in body.model_fields_set,
    )
    return UserResponse.from_user(user)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "incorrect_password"},
        401: {"model": ErrorResponse},
    },
    summary="Delete your account",
    description=(
        "Requires your current password. The account is deactivated and its personal data replaced "
        "immediately; every session ends. The email address can be registered again."
    ),
)
async def delete_account(
    body: DeleteAccountRequest,
    current: CurrentUser,
    users: UserServiceDep,
    settings: AppSettings,
    response: Response,
) -> None:
    await users.delete_account(user_id=current.user.id, password=body.password.get_secret_value())
    clear_refresh_cookie(response, settings)
