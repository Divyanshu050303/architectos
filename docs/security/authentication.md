# Authentication and authorization: security design

How identities, sessions, tenants and permissions are protected, what must be configured to deploy
safely, and the answers to the security review. API shapes are in [docs/api](../api/).

Code map: `core/domain/identity` (users, passwords, tokens, sessions), `core/domain/organizations`
(tenancy, roles, invitations), `core/domain/audit`, `persistence/` (tables, repositories, migrations),
`apps/api` (HTTP, cookies, rate limits, logging).

## Passwords

- **One policy** (`core/domain/identity/passwords.py`, NIST SP 800-63B / OWASP ASVS): length
  `PASSWORD_MIN_LENGTH` (default 12) to 128; not blank; not in a bundled list of 46k breached
  passwords (SecLists, MIT); not built from the account's email. No composition rules. Input is
  NFKC-normalized before checking and hashing. Every failed rule is reported at once.
- **Argon2id** (argon2-cffi, RFC 9106 low-memory profile: 64 MiB, t=3, p=4). Hashes carry their
  parameters; login transparently upgrades outdated hashes. Corrupt hashes never verify.
- Hashing runs in worker threads, at most `PASSWORD_HASH_CONCURRENCY` (4) at once per process, which
  bounds memory under a burst of logins.
- Plaintext passwords are never stored, logged, returned, audited or echoed in validation errors.

## Emailed tokens (verification, password reset, invitation)

- 32 random bytes (`secrets.token_urlsafe`), stored only as SHA-256 (a fast hash is right for
  full-entropy secrets and allows an indexed lookup).
- Single use, enforced with `SELECT … FOR UPDATE` on the token row: concurrent use yields one success.
- Expiry: verification 24 h, reset 30 min, invitation 7 days (all configurable).
- Issuing a new one revokes outstanding ones for the same account (or email, for invitations).
- Cooldown: at most one verification or reset email per account per 60 s, even before rate limits.
- Links carry the token in the query string of an https URL; the web app's pages that read them send
  no `Referer`. The invitation accept endpoint has the token in its path, as specified: the API logs
  only route templates, and uvicorn's raw-path access log is disabled (see Logging). Any other
  component that logs full URLs (a CDN, a load balancer) must be configured not to, or the endpoint
  should move the token to the body.

## Sessions and tokens

**Access token.** JWT, HS256 with `ACCESS_TOKEN_SECRET` (≥ 32 characters), 15 minutes. The algorithm
is pinned; `iss`, `aud`, `typ`, `sub`, `sid`, `iat`, `exp` are required. Every request also loads the
session named by `sid` and the user: a revoked session or disabled user is refused immediately
(`session_revoked`), not after the token expires. Cost: two indexed reads per request.

**Refresh token.** `<session id>.<256-bit secret>`, only the SHA-256 of the secret is stored on the
`sessions` row. Absolute lifetime `REFRESH_TOKEN_TTL` (30 days) from sign-in; refreshing does not
extend it.

**Rotation and reuse detection** (`SessionService.refresh`), under a row lock on the session:

1. The secret matches the current hash: issue a new secret, keep the old hash as "previous".
2. It matches the previous hash within `REFRESH_REUSE_GRACE` (10 s) of the rotation: a concurrent
   refresh from another tab. Answer `409 refresh_conflict`; nothing is revoked.
3. It matches the previous hash after the grace window: two parties hold the session. Revoke the
   whole session (`token_reuse`), audit it; both the attacker and the user must sign in again.
4. It matches neither: reject without revoking, so knowing a session id alone cannot sign anyone out.
   (Tokens older than the previous one are not detected as reuse; they are simply invalid.)

**Cookie.** `__Secure-architectos_refresh`: HttpOnly, Secure, SameSite=Lax, `Path=/api/v1/auth` (sent
to login, refresh and logout only), `Max-Age` to the session's expiry, cleared on logout and on any
refresh failure. The access token never goes in a cookie and should be held in memory by the client.

**CSRF.** Only login, refresh and logout use the cookie. They require `X-Requested-With: architectos`,
which a browser cannot attach cross-origin without a CORS preflight that the allow-list rejects, and a
present `Origin` must be allow-listed. SameSite=Lax adds defence in depth. Every other endpoint is
authenticated by the Bearer header, which browsers never attach automatically. If the web app and API
are ever on different sites, set `COOKIE_SAMESITE=none` (requires `COOKIE_SECURE=true`); the header
and Origin checks remain the protection.

**Revocation.** Logout (the current session), `DELETE /me/sessions/{id}`, password change (every other
session), password reset (every session), account deletion (every session), token reuse (that session).

## Account enumeration

Registration (`202` either way; the real owner is emailed instead), resend-verification and
forgot-password (`202` always) never reveal whether an account exists. Login returns the same
`invalid_credentials` for a wrong password and an unknown email, and both cost one Argon2 verification
(unknown emails verify against a dummy hash). `account_disabled` is only returned for the correct
password. Emails are sent after the response, so mail-server latency is not observable.

## Multi-tenancy

Every organization-scoped request resolves the caller's membership with one query on organization id
**and** authenticated user id **and** organization not deleted, before anything else. The organization
id from the URL is never used on its own. A non-member gets `404 organization_not_found`, identical
to a missing organization. Member and invitation ids are always looked up together with the
organization id from the URL, so another tenant's ids are simply not found.

Future organization-owned resources (projects, architectures) must follow the same pattern:
`WHERE resource.id = :id AND resource.organization_id = :organization_id`, with the organization
resolved from the caller's membership. `tests/security/test_tenant_isolation_sweep.py` covers every
`/organizations/{organization_id}/…` endpoint automatically.

## Authorization

- One role → permission matrix (`core/domain/organizations/permissions.py`); no code compares role
  names. Routes declare `require_permission(Permission.X)`; services check again.
- Target-dependent rules (`membership_policy.py`, exhaustively tested): owners manage anyone; everyone
  else only lower ranks and lower roles; nobody changes their own role; no owner invitations; at least
  one owner always.
- **Concurrency.** Every membership or invitation change locks the organization row and re-reads the
  caller's and the target's memberships under the lock, so decisions use current roles and concurrent
  demotions or departures cannot leave an organization ownerless (tested with real transactions).
- Unverified users cannot create organizations or accept invitations.

## Audit log

- Written through the unit of work inside the transaction of the change it describes: committed and
  rolled back together. Failed logins on real accounts are recorded (committed while the login is
  refused); unknown emails are not.
- `audit_logs` has no foreign keys (entries outlive what they describe) and a trigger that rejects
  UPDATE, DELETE and TRUNCATE.
- Entries carry actor, organization, action, resource, metadata, IP and user agent. A guard refuses
  metadata keys that look like secrets. Invitation entries include the invitee's email.
- Readable per organization by owners and admins (`audit.read`), keyset-paginated.

## Rate limiting

Fixed-window counters in Redis (atomic Lua increment-and-expire), keyed by SHA-256 of IP, email or
user id. Limits are in [the API document](../api/authentication.md#rate-limits); the per-email login
limit also blocks the correct password once reached. If Redis is unreachable the request is allowed
(fail open) and an error is logged: availability over strictness, with Argon2 still slowing guesses.
Fixed windows allow up to twice a limit across a window boundary.

## Logging and observability

- JSON, one object per line, with the request id. One line per request: method, **route template**
  (`/api/v1/invitations/{invitation_token}/accept`), status, duration. No IPs, user agents, headers,
  cookies or bodies in request logs.
- Refusals (401, 403, 409, 429) are logged with error code and route template: the signal for
  alerting on brute force, abuse and authorization failures. Rate-limit hits and limiter outages are
  logged by `architectos.security`. Email delivery failures are logged without recipient or content.
- Never logged anywhere: passwords, tokens, cookies, Authorization headers, hashes. Tests scan logs
  for passwords and tokens.

## HTTP hardening

Security headers on every response (including unhandled 500s): `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, COOP/CORP, `Content-Security-Policy:
default-src 'none'; frame-ancestors 'none'` and `Cache-Control: no-store` on API responses, HSTS in
production. Bodies over 64 KiB are refused before parsing. Validation errors never echo input. 500s
reveal nothing but the request id. API docs are off in production unless enabled.

## Account deletion and retention

`DELETE /me` (password required) is a soft delete in one transaction: status `deleted`, email replaced
by `deleted+<id>@deleted.invalid` (the address can be registered again), name replaced, avatar and
verification cleared, password hash replaced by a value that never verifies, every session and
emailed token revoked, memberships removed, organizations the user was alone in soft-deleted. It is
refused while the user is the only owner of an organization with other members.

Retained: the scrubbed user row (so audit entries and invitations resolve), soft-deleted
organizations, and audit entries (ids, IPs, user agents, invitee emails) indefinitely. **Decide a
retention period** for audit entries, expired sessions and used tokens, publish it in the privacy
policy, and implement the purge as a scheduled job (`workers/`).

## Deployment checklist

1. `ENVIRONMENT=production`. Startup refuses plain-text SMTP, an http `FRONTEND_URL`, insecure cookies,
   and rate limiting without `REDIS_URL`.
2. Secrets from a secret manager, never the repository: `ACCESS_TOKEN_SECRET` (unique per environment,
   ≥ 32 random characters), `DATABASE_URL`, `SMTP_PASSWORD`. Rotating `ACCESS_TOKEN_SECRET` only
   forces clients to refresh (≤ 15 minutes of tokens); sessions survive.
3. TLS everywhere; `FRONTEND_URL` and `CORS_ALLOWED_ORIGINS` set to the real web app origin(s).
4. **Behind a proxy or load balancer**, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy
   addresses>`. Without it, every client appears to come from the proxy: per-IP rate limits apply to
   everyone at once, and sessions and audit entries record the proxy's IP.
5. Run with `--no-access-log` (as `make run` does), and make sure the proxy, CDN or load balancer does
   not log full request URLs (invitation tokens are in a path).
6. Database role: the application needs SELECT, INSERT, UPDATE and row removal on its tables, but only
   SELECT and INSERT on `audit_logs` (the trigger alone can be disabled by a superuser). Migrations run
   with a separate, more privileged role (`alembic upgrade head`).
7. Redis reachable only from the API; SMTP relay with TLS (`SMTP_SECURITY=starttls|tls`) and a verified
   sending domain (SPF, DKIM, DMARC).
8. Set `AVATAR_URL_ALLOWED_HOSTS` to your image host(s).
9. Alert on: rate of `request refused` by code, `rate limit exceeded`, `rate limiter unavailable`,
   `email delivery failed`, `session.revoked` with reason `token_reuse`.

## Security review

| Question | Answer |
|---|---|
| Can a user access another organization? | No. Membership is resolved with organization id and the authenticated user id in one query; non-members get 404 on every scoped endpoint, member/invitation ids are organization-scoped. Swept over every endpoint. |
| Can a user escalate their role? | No. Central matrix plus hierarchy rules; no self role changes; no owner invitations; request bodies reject undeclared fields (mass-assignment sweep). |
| Can a refresh token be replayed? | A replayed rotated-out token revokes the session (after a 10 s grace for concurrent tabs). Rotation is row-locked. |
| Can password reset tokens be reused? | No. Single-use under a row lock; used, superseded and expired tokens are refused. |
| Can verification tokens be reused? | No. Same mechanism. |
| Can a revoked session authenticate? | No. Every request checks the session behind the access token. |
| Can a viewer mutate data? | No. Viewers hold read permissions only (tested for every permission). |
| Can an admin perform owner-only operations? | No. Removing the organization, granting ownership, and managing owners or admins are owner-only. |
| Can invitation tokens be reused? | No. Single-use under a row lock, bound to the invited email, which must be verified. |
| Can an attacker enumerate users? | Not through register, resend, forgot-password or login (identical responses and costs). The rate limits bound other probing. Members of an organization can see each other's emails by design. |
| Can sensitive information appear in logs? | Our logs contain route templates, codes and ids only; tests scan logs and responses for passwords and tokens. External components that log full URLs must be configured (checklist 5). |
| Can CSRF occur? | Cookie endpoints need a custom header (forcing a preflight the CORS allow-list rejects) and an allow-listed Origin; the rest use Bearer tokens browsers never send automatically. |
| Can rate limits be bypassed trivially? | Not by rotating emails (per-IP limit) or IPs (per-email limit). A distributed attack across many IPs and emails is bounded per account; behind a proxy, configure forwarded headers (checklist 4). Fixed windows allow short bursts at boundaries. |
| Can concurrent requests violate organization invariants? | No. Membership changes lock the organization and re-read roles; tested with concurrent mutual demotion and simultaneous leaving. |

## Tests

852 tests (unit, integration against Postgres and Redis, HTTP, security sweeps, query budgets). Every
item of the security test matrix and edge-case list maps to named tests in
`tests/security/test_traceability.py`. Run `make test-security` for the sweeps and `make coverage` for
line coverage (98%).

## Known limitations

- Fixed-window rate limits; fail-open when Redis is down.
- Email is sent from the API process after the response; a crash between commit and delivery loses
  that email (the user can request it again). An outbox table processed by a worker would make
  delivery durable.
- Refresh tokens older than the previous generation are rejected but not treated as theft.
- No MFA, OAuth sign-in or verified email change yet.
- Audit and session retention periods are not decided (see above).
