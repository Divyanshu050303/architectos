"""architectures and revisions

Adds a project's architecture (at most one per project), its append-only revisions (the validated
Architecture IR as canonical JSON, with its schema version, content hash, parent, source, summary,
reason and the requirement set it was designed against) and its layout (presentation only, never
versioned). Composite foreign keys keep an architecture, its revisions, its layout and the
referenced requirement set in one project; a deferred foreign key guarantees the current
revision exists; each revision's parent is the previous one.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26 07:23:09.533044+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "architectures",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("current_revision >= 1", name=op.f("ck_architectures_current_revision_positive")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_architectures_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_architectures")),
        sa.UniqueConstraint("id", "project_id", name=op.f("uq_architectures_id_project_id")),
        sa.UniqueConstraint("project_id", name=op.f("uq_architectures_project_id")),
    )
    op.create_table(
        "architecture_revisions",
        sa.Column("architecture_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_number", sa.Integer(), nullable=True),
        sa.Column("ir", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ir_schema_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requirement_set_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_architecture_revisions_content_hash_format")
        ),
        sa.CheckConstraint("jsonb_typeof(ir) = 'object'", name=op.f("ck_architecture_revisions_ir_object")),
        sa.CheckConstraint(
            "source IN ('user', 'ai', 'discovery', 'import', 'system')",
            name=op.f("ck_architecture_revisions_source_valid"),
        ),
        sa.CheckConstraint(
            "(number = 1 AND parent_number IS NULL) OR parent_number = number - 1",
            name=op.f("ck_architecture_revisions_parent_is_previous"),
        ),
        sa.CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 500", name=op.f("ck_architecture_revisions_summary_length")
        ),
        sa.CheckConstraint(
            "ir_schema_version >= 1", name=op.f("ck_architecture_revisions_ir_schema_version_positive")
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_architecture_revisions_number_positive")),
        sa.CheckConstraint(
            "octet_length(ir::text) <= 16777216", name=op.f("ck_architecture_revisions_ir_size")
        ),
        sa.CheckConstraint(
            "reason IS NULL OR char_length(reason) BETWEEN 1 AND 500",
            name=op.f("ck_architecture_revisions_reason_length"),
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "parent_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architecture_revisions_parent_architecture_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_revisions_architecture_architectures",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_set_id", "project_id"],
            ["requirement_sets.id", "requirement_sets.project_id"],
            name="fk_architecture_revisions_requirement_set_requirement_sets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_architecture_revisions")),
        sa.UniqueConstraint(
            "architecture_id", "number", name=op.f("uq_architecture_revisions_architecture_id_number")
        ),
        sa.UniqueConstraint("project_id", "number", name=op.f("uq_architecture_revisions_project_id_number")),
    )
    # The current revision exists (checked at commit: the architecture is inserted first).
    op.create_foreign_key(
        "fk_architectures_current_revision_architecture_revisions",
        "architectures",
        "architecture_revisions",
        ["id", "current_revision"],
        ["architecture_id", "number"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "architecture_layouts",
        sa.Column("architecture_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("positions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(positions) = 'object'", name=op.f("ck_architecture_layouts_positions_object")
        ),
        sa.CheckConstraint(
            "octet_length(positions::text) <= 1048576", name=op.f("ck_architecture_layouts_positions_size")
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_layouts_architecture_architectures",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("architecture_id", name=op.f("pk_architecture_layouts")),
    )
    # Revisions are append-only, with the guard function created for requirement sets (0004).
    op.execute(
        """
        CREATE TRIGGER architecture_revisions_append_only
        BEFORE UPDATE OR DELETE ON architecture_revisions
        FOR EACH ROW EXECUTE FUNCTION requirement_sets_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER architecture_revisions_no_truncate
        BEFORE TRUNCATE ON architecture_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION requirement_sets_reject_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER architecture_revisions_no_truncate ON architecture_revisions")
    op.execute("DROP TRIGGER architecture_revisions_append_only ON architecture_revisions")
    op.drop_table("architecture_layouts")
    op.drop_constraint(
        "fk_architectures_current_revision_architecture_revisions", "architectures", type_="foreignkey"
    )
    op.drop_table("architecture_revisions")
    op.drop_table("architectures")
