"""project architecture policy

Adds projects.architecture_policy: the project's typed architecture policy (allowed and
prohibited technologies, allowed regions, TLS required, maximum component count), validated by the
domain (ArchitecturePolicy); the database only guarantees a JSON object. Existing projects get the
empty policy, which constrains nothing.

Downgrading removes the column and with it any policies set since.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-26 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "architecture_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_projects_architecture_policy_object"),
        "projects",
        "jsonb_typeof(architecture_policy) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_projects_architecture_policy_object"), "projects", type_="check")
    op.drop_column("projects", "architecture_policy")
