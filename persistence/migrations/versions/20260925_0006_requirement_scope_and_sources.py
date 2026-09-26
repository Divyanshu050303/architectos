"""requirement scope and sources

For the Requirements Engine: requirements and versions get a ``scope`` (default ``system``, which
also means "unspecified"), the sources ``imported``, ``discovery`` and ``system`` join ``user`` and
``ai``, machine interpretations (``ai``, ``discovery``) must carry a confidence and a person's own
requirement must not. Adding a column with a constant default rewrites no rows (and so fires no
append-only trigger on requirement_versions); existing rows satisfy every new check.

Downgrading fails, by design, if any requirement already uses a new source or scope.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("requirements", "requirement_versions")
_OLD_SOURCES = "source IN ('user', 'ai')"
_NEW_SOURCES = "source IN ('user', 'ai', 'imported', 'discovery', 'system')"
_SCOPES = "scope IN ('system', 'service', 'api', 'database', 'queue', 'user', 'region', 'data')"


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("scope", sa.Text(), server_default="system", nullable=False))
        op.create_check_constraint(op.f(f"ck_{table}_scope"), table, _SCOPES)
        op.drop_constraint(op.f(f"ck_{table}_source"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_source"), table, _NEW_SOURCES)
        op.drop_constraint(op.f(f"ck_{table}_ai_confidence"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_machine_confidence"),
            table,
            "source NOT IN ('ai', 'discovery') OR confidence IS NOT NULL",
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_user_without_confidence"), table, "source <> 'user' OR confidence IS NULL"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(op.f(f"ck_{table}_user_without_confidence"), table, type_="check")
        op.drop_constraint(op.f(f"ck_{table}_machine_confidence"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_ai_confidence"), table, "source <> 'ai' OR confidence IS NOT NULL"
        )
        op.drop_constraint(op.f(f"ck_{table}_source"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_source"), table, _OLD_SOURCES)
        op.drop_constraint(op.f(f"ck_{table}_scope"), table, type_="check")
        op.drop_column(table, "scope")
