# ArchitectOS

Architecture intelligence platform. This repository holds the web app (`apps/web`, see its README)
and the backend: API (`apps/api`), domain (`core/`), persistence (`persistence/`).

## Backend quick start

Requirements: Python 3.14 with [uv](https://docs.astral.sh/uv/), Docker.

```bash
make install        # dependencies into .venv
make db-up          # Postgres (5434), Redis (6380), Mailpit (SMTP 1025, inbox http://127.0.0.1:8025)
cp .env.example .env
# set ACCESS_TOKEN_SECRET: python -c "import secrets; print(secrets.token_urlsafe(48))"
make migrate        # apply database migrations
make run            # API on http://localhost:8000 (docs: /api/docs); API_PORT=8001 to change
```

Emails in development land in Mailpit, not real inboxes.

## Checks

```bash
make check          # lint + strict type checking + every test
make test-unit | test-integration | test-api | test-security | migrate-check | coverage
```

## Documentation

- [Authentication API](docs/api/authentication.md) and [user and organization management API](docs/api/user-management.md)
- [Security design, deployment checklist and security review](docs/security/authentication.md)
- ADRs: [session and token model](docs/adr/ADR-005-session-and-token-model.md),
  [tenancy and authorization](docs/adr/ADR-006-tenancy-and-authorization.md)
