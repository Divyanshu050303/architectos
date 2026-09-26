"""capacity analyses

A capacity analysis records one execution of the deterministic capacity engine against one
architecture revision and one workload: the request's inputs (the workload snapshot, models,
parameters, assumptions, entries), the model set, fingerprints, summary, demand per connection,
unsupported calculations, limitations, scaling options and scenario outcomes. Components and
bottleneck candidates are rows of their own, for paging and filtering.

The three tables are append-only (triggers, with the guard function of requirement sets, 0004): an
analysis is stored once, finished. Analyses reference their architecture and revision, and rows
their analysis, through same-project foreign keys.

Downgrading removes the three tables and every stored analysis with them.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-26 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

TABLES = ("capacity_analyses", "capacity_components", "capacity_bottlenecks")

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capacity_analyses",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("architecture_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_content_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_set", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_fingerprint", sa.Text(), nullable=True),
        sa.Column("result_fingerprint", sa.Text(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("connections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unsupported", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scaling", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unsupported_scaling", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scenarios", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(inputs) = 'object'", name=op.f("ck_capacity_analyses_inputs_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scenarios) = 'array'", name=op.f("ck_capacity_analyses_scenarios_array")
        ),
        sa.CheckConstraint(
            "revision_content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_capacity_analyses_revision_content_hash_format"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name=op.f("ck_capacity_analyses_failed_has_error"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'insufficient_input', 'unsupported', 'failed')",
            name=op.f("ck_capacity_analyses_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL AND result_fingerprint IS NOT NULL AND summary IS NOT NULL AND model_set IS NOT NULL)",
            name=op.f("ck_capacity_analyses_finished_has_result"),
        ),
        sa.CheckConstraint(
            "label IS NULL OR char_length(label) BETWEEN 1 AND 100",
            name=op.f("ck_capacity_analyses_label_length"),
        ),
        sa.CheckConstraint(
            "revision_number >= 1", name=op.f("ck_capacity_analyses_revision_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_capacity_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_capacity_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capacity_analyses")),
        sa.UniqueConstraint("id", "project_id", name=op.f("uq_capacity_analyses_id_project_id")),
    )
    op.create_index(
        op.f("ix_capacity_analyses_architecture_id_requested_at_id"),
        "capacity_analyses",
        ["architecture_id", "requested_at", "id"],
        unique=False,
    )
    op.create_table(
        "capacity_bottlenecks",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("certainty", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "certainty IN ('modeled', 'candidate')", name=op.f("ck_capacity_bottlenecks_certainty")
        ),
        sa.CheckConstraint(
            "condition IN ('exceeds_capacity', 'at_capacity', 'above_target', 'no_capacity', 'unknown_capacity')",
            name=op.f("ck_capacity_bottlenecks_condition"),
        ),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name=op.f("ck_capacity_bottlenecks_data_object")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_capacity_bottlenecks_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["capacity_analyses.id", "capacity_analyses.project_id"],
            name="fk_capacity_bottlenecks_analysis_capacity_analyses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capacity_bottlenecks")),
        sa.UniqueConstraint(
            "analysis_id", "position", name=op.f("uq_capacity_bottlenecks_analysis_id_position")
        ),
    )
    op.create_table(
        "capacity_components",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name=op.f("ck_capacity_components_data_object")),
        sa.CheckConstraint(
            "status IN ('estimated', 'insufficient_input', 'unsupported')",
            name=op.f("ck_capacity_components_status"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["capacity_analyses.id", "capacity_analyses.project_id"],
            name="fk_capacity_components_analysis_capacity_analyses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capacity_components")),
        sa.UniqueConstraint(
            "analysis_id", "node_id", name=op.f("uq_capacity_components_analysis_id_node_id")
        ),
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION requirement_sets_reject_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION requirement_sets_reject_change()"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.drop_table("capacity_components")
    op.drop_table("capacity_bottlenecks")
    op.drop_index(
        op.f("ix_capacity_analyses_architecture_id_requested_at_id"), table_name="capacity_analyses"
    )
    op.drop_table("capacity_analyses")
