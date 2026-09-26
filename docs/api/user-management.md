# User and organization management API

Conventions, tokens, error envelope and rate limits: see [authentication.md](authentication.md).
Every endpoint here requires `Authorization: Bearer <accessToken>`.

## Your account

| Method and path | Body | Success | Errors |
|---|---|---|---|
| `GET /me` | | `200 User` | |
| `PATCH /me` | `{name?, avatarUrl?}` (`avatarUrl: null` removes it) | `200 User` | `422 nothing_to_update, invalid_name, invalid_avatar_url, validation_error` (email/status cannot be set) |
| `PATCH /me/password` | `{currentPassword, newPassword}` | `204` | `400 incorrect_password`, `422 weak_password` |
| `DELETE /me` | `{password}` | `204` + refresh cookie cleared | `400 incorrect_password`, `409 sole_owner_of_organization` |
| `GET /me/sessions` | | `200 {sessions: [{id, createdAt, lastUsedAt, expiresAt, userAgent, ipAddress, current}]}` | |
| `DELETE /me/sessions/{sessionId}` | | `204` (clears the cookie if it was this session) | `404 session_not_found` |

- **Avatar URLs** must be `https`, without credentials, at most 2048 characters. With
  `AVATAR_URL_ALLOWED_HOSTS` set, only those hosts are accepted.
- **Changing your password** keeps this session signed in (its refresh token is unchanged) and signs
  out every other session.
- **Deleting your account** is refused while you are the only owner of an organization with other
  members (`details.organizationIds`); transfer ownership first. Organizations you are alone in are
  deleted with the account. See [the security document](../security/authentication.md#account-deletion-and-retention).

## Organizations

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `GET /organizations` | | `200 {organizations: [{id, name, role, createdAt, updatedAt}]}` | |
| `POST /organizations` `{name}` | verified email | `201 Organization` (you are `owner`) | `403 email_not_verified`, `422 invalid_organization_name` |
| `GET /organizations/{id}` | `organization.read` | `200 Organization` | |
| `PATCH /organizations/{id}` `{name}` | `organization.update` | `200 Organization` | `422 invalid_organization_name` |
| `DELETE /organizations/{id}` | `organization.delete` | `204` (soft delete, for every member) | |

On every `/organizations/{id}/…` endpoint: **`404 organization_not_found` when the organization does
not exist or you are not a member** (existence is never revealed), `403 permission_denied` when your
role lacks the permission.

## Members

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `GET /organizations/{id}/members` | `member.read` | `200 {members: [{id, userId, name, email, avatarUrl, role, joinedAt}]}`, owners first | |
| `PATCH /organizations/{id}/members/{memberId}` `{role}` | `member.update_role` | `200 {id, userId, role}` | `403 cannot_change_own_role, role_not_manageable`, `404 member_not_found`, `409 last_owner` |
| `DELETE /organizations/{id}/members/{memberId}` | `member.remove`, or none to leave | `204` | `403 role_not_manageable`, `404 member_not_found`, `409 last_owner` |

`memberId` is the membership `id` from the list. Using your own membership id with `DELETE` means
leaving the organization.

## Invitations

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `GET /organizations/{id}/invitations` | `member.invite` | `200 {invitations: [{id, email, role, invitedByUserId, expiresAt, createdAt}]}` | |
| `POST /organizations/{id}/invitations` `{email, role}` | `member.invite` | `201 Invitation` (the token is only emailed) | `403 role_not_manageable`, `409 already_member`, `422 owner_invitation_not_allowed, invalid_email` |
| `DELETE /organizations/{id}/invitations/{invitationId}` | `member.invite` | `204` | `404 invitation_not_found` |
| `POST /invitations/{token}/accept` | signed in, verified, matching email | `200 Organization` with your new role | `403 email_not_verified, invitation_email_mismatch`, `404 invalid_invitation`, `409 already_member`, `410 invitation_expired` |

Inviting an address that already has a pending invitation replaces it (the earlier link stops
working); use this to resend. Links go to `{FRONTEND_URL}/accept-invitation?token=…` and expire after
`INVITATION_TTL` (7 days).

## Audit log

`GET /organizations/{id}/audit-log?limit=1..100&cursor=…` (`audit.read`) returns
`{entries: [{id, action, actorUserId, resourceType, resourceId, metadata, ipAddress, userAgent, createdAt}], nextCursor}`,
newest first. Pass `nextCursor` back as `cursor` for older entries; `422 invalid_cursor` for a bad one.

Actions: `organization.created|updated|deleted`, `member.invited|invitation_revoked|invitation_accepted|role_changed|removed`.
Account-level events (`user.*`, `session.revoked`) are recorded too, without an organization.

## Roles

| | Owner | Admin | Member | Viewer |
|---|:-:|:-:|:-:|:-:|
| `organization.read`, `member.read`, `project.read`, `architecture.read` | ✓ | ✓ | ✓ | ✓ |
| `project.create`, `project.update`, `architecture.create`, `.update`, `.validate`, `.analyze`, `.simulate`, `.evolve` | ✓ | ✓ | ✓ | |
| `organization.update`, `member.invite`, `member.remove`, `member.update_role`, `audit.read`, `pricing.manage`, `project.delete`, `project.policy_update`, `architecture.delete` | ✓ | ✓ | | |
| `organization.delete` | ✓ | | | |

Rules on top of the matrix:

- Owners manage anyone (including other owners) and may grant ownership.
- Everyone else acts only on members ranked below them and assigns only roles below their own: an
  admin manages members and viewers, never admins or owners, and cannot grant admin or owner.
- Nobody changes their own role. Nobody is invited as owner; ownership is granted by a role change.
- An organization always keeps at least one owner (`409 last_owner`).
