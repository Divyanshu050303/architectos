# ADR-005: Session and token model

- Status: accepted
- Date: 2026-09-24

## Context

The web app (Next.js, `apps/web`) and the API (`apps/api`) need authentication that resists token
theft, XSS and CSRF, supports immediate revocation (sign out a device, password reset) and works for
several tabs at once. The frontend had proposed a single cookie session; the specification asked for
short-lived access tokens with rotating refresh tokens.

## Decision

- **Access token**: JWT, HS256, 15 minutes, returned in the response body and held **in memory** by
  the client; sent as `Authorization: Bearer`. Every request also checks the session it names is
  still active, so revocation is immediate.
- **Refresh token**: opaque `<session id>.<256-bit secret>` in an **HttpOnly, Secure, SameSite=Lax
  cookie scoped to `/api/v1/auth`**; only SHA-256 of the secret is stored. Rotated on every refresh
  under a row lock; the previous hash is kept to detect reuse. Reuse within 10 s is a concurrent
  refresh (`409`, nothing revoked); later reuse revokes the session. Absolute 30-day lifetime.
- **CSRF**: cookie endpoints require `X-Requested-With: architectos` and an allow-listed `Origin`.
- Unverified users may sign in but cannot create organizations or accept invitations.

## Consequences

- XSS cannot read the refresh token; a stolen access token is useful for at most 15 minutes and is
  cut off by revocation. Refresh-token theft is detected at the next rotation by either party.
- One indexed session read per authenticated request (the price of immediate revocation).
- Clients must handle `access_token_expired` (refresh and retry) and `refresh_conflict` (retry).
- Rotating the signing secret costs clients one refresh; sessions survive.
- Rejected alternatives: long-lived JWTs (no revocation), access token in a cookie (CSRF on every
  endpoint), refresh token in `localStorage` (readable by XSS), sliding session expiry (unbounded
  session lifetime).
