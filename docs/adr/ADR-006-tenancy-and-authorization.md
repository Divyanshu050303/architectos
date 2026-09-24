# ADR-006: Tenancy and authorization

- Status: accepted
- Date: 2026-09-24

## Context

Organizations are tenants. Every future resource (projects, architectures) belongs to one. Mistakes
here leak one customer's data to another, so the rules must be structural rather than remembered in
each route.

## Decision

- **Membership first.** Every organization-scoped request resolves the authenticated user's membership
  with a single query on organization id, user id and "organization not deleted". Client-supplied
  organization ids are never trusted on their own. Non-members get **404** (not 403), so existence
  cannot be probed. Child resources are always queried together with the organization id.
- **One permission matrix** (`core/domain/organizations/permissions.py`), roles OWNER > ADMIN > MEMBER
  > VIEWER. Routes declare `require_permission(...)`; services check again. No code compares role names.
- **Hierarchy rules** (`membership_policy.py`): non-owners act only on lower ranks and assign only lower
  roles; nobody changes their own role; nobody is invited as owner; at least one owner always.
- **Serialization.** Membership and invitation changes lock the organization row and re-read roles
  under the lock, making the invariants hold under concurrency.
- **Soft deletion** of organizations; audit entries are append-only and outlive what they describe.

## Consequences

- New organization-owned endpoints live under `/organizations/{organization_id}/…` and get tenant
  isolation from the shared dependency; `tests/security/test_tenant_isolation_sweep.py` checks each one
  automatically.
- Membership changes to one organization are serialized (negligible at realistic rates).
- A permission change is a one-line matrix edit, with an explicit test table to update deliberately.
