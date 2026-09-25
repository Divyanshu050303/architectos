"""projects

Adds the projects table: tenant-owned (organization_id RESTRICT), slug unique per organization among
non-deleted projects, lifecycle active -> archived -> deleted (soft, only from archived).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25 16:47:15.182086+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)", name=op.f("ck_projects_archived_state")
        ),
        sa.CheckConstraint(
            "char_length(slug) BETWEEN 1 AND 63 AND slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name=op.f("ck_projects_slug_format"),
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR status = 'archived'", name=op.f("ck_projects_deleted_requires_archived")
        ),
        sa.CheckConstraint("jsonb_typeof(settings) = 'object'", name=op.f("ck_projects_settings_object")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_projects_status")),
        sa.CheckConstraint("char_length(description) <= 2000", name=op.f("ck_projects_description_length")),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100", name=op.f("ck_projects_name_length")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_projects_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_projects_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(
        op.f("ix_projects_organization_id_status_created_at_id"),
        "projects",
        ["organization_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_projects_organization_id_slug_live",
        "projects",
        ["organization_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_projects_organization_id_slug_live",
        table_name="projects",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_projects_organization_id_status_created_at_id"), table_name="projects")
    op.drop_table("projects")
