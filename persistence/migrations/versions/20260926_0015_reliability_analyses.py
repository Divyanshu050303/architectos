"""reliability analyses

A reliability analysis records one execution of the deterministic reliability engine against one
architecture revision: the request's inputs (entries, objectives, assumptions) and the in-force
requirements it read (id, version, status), the model set, fingerprints, summary, request paths,
objective verdicts, unsupported calculations and limitations. Components and findings are rows of
their own, for paging and filtering; findings keep their canonical position and stable id.

The three tables are append-only (triggers, with the guard function of requirement sets, 0004): an
analysis is stored once, finished. Analyses reference their architecture and revision, and rows
their analysis, through same-project foreign keys.

Downgrading removes the three tables and every stored reliability analysis with them.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26 23:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

TABLES = ("reliability_analyses", "reliability_components", "reliability_findings")

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reliability_analyses",
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
        sa.Column("paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("objectives", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unsupported", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(inputs) = 'object'", name=op.f("ck_reliability_analyses_inputs_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(objectives) = 'array'", name=op.f("ck_reliability_analyses_objectives_array")
        ),
        sa.CheckConstraint("jsonb_typeof(paths) = 'array'", name=op.f("ck_reliability_analyses_paths_array")),
        sa.CheckConstraint(
            "revision_content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reliability_analyses_revision_content_hash_format"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name=op.f("ck_reliability_analyses_failed_has_error"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'insufficient_input', 'unsupported', 'failed')",
            name=op.f("ck_reliability_analyses_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL AND result_fingerprint IS NOT NULL AND summary IS NOT NULL AND model_set IS NOT NULL)",
            name=op.f("ck_reliability_analyses_finished_has_result"),
        ),
        sa.CheckConstraint(
            "label IS NULL OR char_length(label) BETWEEN 1 AND 100",
            name=op.f("ck_reliability_analyses_label_length"),
        ),
        sa.CheckConstraint(
            "revision_number >= 1", name=op.f("ck_reliability_analyses_revision_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_reliability_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_reliability_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reliability_analyses")),
        sa.UniqueConstraint("id", "project_id", name=op.f("uq_reliability_analyses_id_project_id")),
    )
    op.create_index(
        op.f("ix_reliability_analyses_architecture_id_requested_at_id"),
        "reliability_analyses",
        ["architecture_id", "requested_at", "id"],
        unique=False,
    )
    op.create_table(
        "reliability_components",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(data) = 'object'", name=op.f("ck_reliability_components_data_object")
        ),
        sa.CheckConstraint(
            "status IN ('estimated', 'insufficient_input', 'unsupported')",
            name=op.f("ck_reliability_components_status"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["reliability_analyses.id", "reliability_analyses.project_id"],
            name="fk_reliability_components_analysis_reliability_analyses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reliability_components")),
        sa.UniqueConstraint(
            "analysis_id", "node_id", name=op.f("uq_reliability_components_analysis_id_node_id")
        ),
    )
    op.create_table(
        "reliability_findings",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("finding_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("certainty", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "certainty IN ('modeled', 'candidate')", name=op.f("ck_reliability_findings_certainty")
        ),
        sa.CheckConstraint(
            "finding_id ~ '^rel_[0-9a-f]{16}$'", name=op.f("ck_reliability_findings_finding_id_format")
        ),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name=op.f("ck_reliability_findings_data_object")),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name=op.f("ck_reliability_findings_severity"),
        ),
        sa.CheckConstraint(
            "type IN ('single_point_of_failure', 'no_redundancy', 'critical_dependency_without_alternative', 'redundancy_without_failure_domain_separation', 'potential_correlated_failure', 'inconsistent_redundancy', 'missing_failover', 'missing_recovery_data', 'recovery_exceeds_objective', 'data_loss_exceeds_objective', 'availability_below_objective', 'objective_not_evaluable', 'unmodeled_dependency', 'circular_dependency', 'availability_not_evaluable', 'unverified_reliability_data', 'redundancy_below_objective')",
            name=op.f("ck_reliability_findings_type"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_reliability_findings_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["reliability_analyses.id", "reliability_analyses.project_id"],
            name="fk_reliability_findings_analysis_reliability_analyses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reliability_findings")),
        sa.UniqueConstraint(
            "analysis_id", "finding_id", name=op.f("uq_reliability_findings_analysis_id_finding_id")
        ),
        sa.UniqueConstraint(
            "analysis_id", "position", name=op.f("uq_reliability_findings_analysis_id_position")
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
    op.drop_table("reliability_findings")
    op.drop_table("reliability_components")
    op.drop_index(
        op.f("ix_reliability_analyses_architecture_id_requested_at_id"), table_name="reliability_analyses"
    )
    op.drop_table("reliability_analyses")
