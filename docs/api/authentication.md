# Authentication API

Base path: `/api/v1`. Interactive reference: `/api/docs` (disabled in production by default).

## Conventions

- **JSON in camelCase.** Request bodies reject unknown fields (`422 validation_error`).
- **Every error** has one shape, and the `X-Request-ID` response header repeats `request_id`:

  ```json
  { "error": { "code": "invalid_credentials", "message": "Email or password is incorrect.",
               "details": null, "request_id": "req_3502d9348c9f4f2eb776d31dc0ccdd4a" } }
  ```

  Clients branch on `code` (stable, lower_snake); `message` is safe to show to users.
- **Request ids.** Send `X-Request-ID` (8–128 of `[A-Za-z0-9._-]`) to correlate; otherwise one is generated.
- **Cookie endpoints** (`login`, `refresh`, `logout`) require `X-Requested-With: architectos`, and a
  browser `Origin` must be in `CORS_ALLOWED_ORIGINS` (`403 csrf_rejected` otherwise). Browsers must send
  them with `credentials: "include"`.
- **Protected endpoints** take `Authorization: Bearer <accessToken>`.
- **Rate limits** answer `429 rate_limited` with `Retry-After` (seconds) and `details.retryAfter`.

## Tokens

| | Access token | Refresh token |
|---|---|---|
| Form | JWT (HS256), claims `sub` (user), `sid` (session), `iss`, `aud`, `typ`, `iat`, `exp` | `<sessionId>.<256-bit secret>`, opaque |
| Where | Response body; keep it **in memory** only | `__Secure-architectos_refresh` cookie: HttpOnly, Secure, SameSite=Lax, `Path=/api/v1/auth` |
| Lifetime | `ACCESS_TOKEN_TTL` (15 min) | `REFRESH_TOKEN_TTL` (30 days from sign-in, not extended) |
| Stored server-side | Not stored; every request checks its session is still active | SHA-256 of the secret only |

## Endpoints

| Method and path | Body | Success | Errors |
|---|---|---|---|
| `POST /auth/register` | `{email, password, name}` | `202 {message}`, same for new and existing emails | `422 invalid_email, invalid_name, weak_password` |
| `POST /auth/verify-email` | `{token}` | `200 {message}` | `400 invalid_token, token_expired` |
| `POST /auth/resend-verification` | `{email}` | `202 {message}`, always | n/a |
| `POST /auth/login` | `{email, password}` | `200 SessionResponse` + cookie | `401 invalid_credentials`, `403 account_disabled, csrf_rejected` |
| `POST /auth/refresh` | none (cookie) | `200 SessionResponse` + rotated cookie | `401 invalid_refresh_token, session_expired` (cookie cleared), `409 refresh_conflict` |
| `POST /auth/logout` | none (cookie) | `204` + cookie cleared, always | `403 csrf_rejected` |
| `POST /auth/forgot-password` | `{email}` | `202 {message}`, always | n/a |
| `POST /auth/reset-password` | `{token, password}` | `200 {message}` | `400 invalid_token, token_expired`, `422 weak_password` (link still usable) |

`SessionResponse`: `{accessToken, tokenType: "Bearer", expiresIn, user}` where `user` is
`{id, email, name, avatarUrl, emailVerified, createdAt}`. Responses are `Cache-Control: no-store`.

Account, session, organization and member endpoints are in [user-management.md](user-management.md).

## Flows

**Sign up.** `register` → the user opens the emailed link `{FRONTEND_URL}/verify-email?token=…` → the
web app posts the token to `verify-email`. Unverified users may sign in but cannot create
organizations or accept invitations (`403 email_not_verified`).

```bash
curl -X POST $API/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"correct horse battery staple","name":"Ada"}'
```

**Sign in.**

```bash
curl -c jar -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' \
  -H 'X-Requested-With: architectos' \
  -d '{"email":"ada@example.com","password":"correct horse battery staple"}'
```

**Browser session handling.**

1. Keep `accessToken` in memory (never `localStorage`). Send it as `Authorization: Bearer …`.
2. On page load, and on `401 access_token_expired`, call `POST /auth/refresh` (with credentials and
   `X-Requested-With`), take the new access token, retry the request once.
3. `409 refresh_conflict`: another tab rotated the cookie a moment ago; retry refresh once.
4. `401` with any other code: the session is over; send the user to sign in.

**Forgotten password.** `forgot-password` → emailed link `{FRONTEND_URL}/reset-password?token=…`
(30 minutes) → `reset-password`. Every session is signed out; a "password changed" notice is emailed.

## Error codes

| Code | Status | Meaning |
|---|---|---|
| `validation_error` | 422 | Malformed JSON, missing/unknown field, wrong type; `details.fields` lists them (never values) |
| `invalid_email`, `invalid_name`, `weak_password` | 422 | Domain validation; `weak_password.details.reasons`: `too_short, too_long, blank, common, contains_email` |
| `invalid_credentials` | 401 | Wrong password **or** unknown email (indistinguishable) |
| `account_disabled` | 403 | Only after the correct password |
| `invalid_token`, `token_expired` | 400 | Email links: unknown/used/superseded, or expired |
| `invalid_refresh_token`, `session_expired`, `refresh_conflict` | 401/401/409 | See browser handling |
| `unauthenticated`, `invalid_access_token`, `access_token_expired`, `session_revoked` | 401 | Bearer problems; `WWW-Authenticate: Bearer` is set |
| `csrf_rejected` | 403 | Missing `X-Requested-With` or a foreign `Origin` on cookie endpoints |
| `rate_limited` | 429 | See limits below |
| `payload_too_large` | 413 | Body over `MAX_REQUEST_BODY_BYTES` (64 KiB) |
| `internal_error` | 500 | Nothing internal is disclosed; quote the request id |

## Rate limits

| Action | Per IP | Per email / user |
|---|---|---|
| login | 30 / 5 min | 10 / 15 min per email (applies even to the right password) |
| register | 10 / hour | |
| refresh | 120 / min | |
| verify-email, reset-password | 30 / hour | |
| resend-verification, forgot-password | 10 / hour | 5 / hour per email |
| change password, delete account | | 10 / hour per user |
| create organization | | 20 / day per user |
| create invitation | | 50 / hour per user |
| accept invitation | 30 / hour | |
