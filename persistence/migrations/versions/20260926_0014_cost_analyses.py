"""cost analyses

A cost analysis records one execution of the deterministic cost engine against one architecture
revision, priced with one pricing snapshot of the organization and, optionally, the usage of one
capacity analysis of the same architecture: the request's inputs (currency, pricing date, operating
hours, assumptions, provider, scenarios), the model set, fingerprints, totals, summary
(breakdowns, unknown items, drivers), unsupported components, limitations, projection assumptions
and scenario projections. Line items are rows of their own, for paging and filtering.

Both tables are append-only (triggers, with the guard function of requirement sets, 0004): an
analysis is stored once, finished. Analyses reference their architecture and revision and their
capacity analysis through same-project foreign keys, and their snapshot through a same-organization
foreign key; line items reference their analysis through a same-project foreign key.

Downgrading removes both tables and every stored cost analysis with them.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-26 22:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

TABLES = ("cost_analyses", "cost_line_items")

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cost_analyses",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("architecture_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_content_hash", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("capacity_analysis_id", sa.Uuid(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_hash", sa.Text(), nullable=True),
        sa.Column("model_set", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_fingerprint", sa.Text(), nullable=True),
        sa.Column("result_fingerprint", sa.Text(), nullable=True),
        sa.Column("totals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("unsupported", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scenarios", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_cost_analyses_currency_format")),
        sa.CheckConstraint("jsonb_typeof(inputs) = 'object'", name=op.f("ck_cost_analyses_inputs_object")),
        sa.CheckConstraint(
            "jsonb_typeof(scenarios) = 'array'", name=op.f("ck_cost_analyses_scenarios_array")
        ),
        sa.CheckConstraint(
            "revision_content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_cost_analyses_revision_content_hash_format"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name=op.f("ck_cost_analyses_failed_has_error"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'insufficient_pricing', 'unsupported', 'failed')",
            name=op.f("ck_cost_analyses_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL AND result_fingerprint IS NOT NULL AND totals IS NOT NULL AND summary IS NOT NULL AND model_set IS NOT NULL)",
            name=op.f("ck_cost_analyses_finished_has_result"),
        ),
        sa.CheckConstraint(
            "label IS NULL OR char_length(label) BETWEEN 1 AND 100",
            name=op.f("ck_cost_analyses_label_length"),
        ),
        sa.CheckConstraint("revision_number >= 1", name=op.f("ck_cost_analyses_revision_number_positive")),
        sa.ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_cost_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_cost_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["capacity_analysis_id", "project_id"],
            ["capacity_analyses.id", "capacity_analyses.project_id"],
            name="fk_cost_analyses_capacity_capacity_analyses",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["pricing_snapshots.id", "pricing_snapshots.organization_id"],
            name="fk_cost_analyses_snapshot_pricing_snapshots",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_analyses")),
        sa.UniqueConstraint("id", "project_id", name=op.f("uq_cost_analyses_id_project_id")),
    )
    op.create_index(
        op.f("ix_cost_analyses_architecture_id_requested_at_id"),
        "cost_analyses",
        ["architecture_id", "requested_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_analyses_capacity_analysis_id"), "cost_analyses", ["capacity_analysis_id"], unique=False
    )
    op.create_index(op.f("ix_cost_analyses_snapshot_id"), "cost_analyses", ["snapshot_id"], unique=False)
    op.create_table(
        "cost_line_items",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "category IN ('compute', 'database', 'storage', 'network', 'managed_service', 'messaging', 'observability', 'other')",
            name=op.f("ck_cost_line_items_category"),
        ),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name=op.f("ck_cost_line_items_data_object")),
        sa.CheckConstraint("kind IN ('fixed', 'usage')", name=op.f("ck_cost_line_items_kind")),
        sa.CheckConstraint("status IN ('priced', 'unknown')", name=op.f("ck_cost_line_items_status")),
        sa.ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["cost_analyses.id", "cost_analyses.project_id"],
            name="fk_cost_line_items_analysis_cost_analyses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_line_items")),
        sa.UniqueConstraint(
            "analysis_id",
            "element_id",
            "resource",
            name=op.f("uq_cost_line_items_analysis_id_element_id_resource"),
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
    op.drop_table("cost_line_items")
    op.drop_index(op.f("ix_cost_analyses_snapshot_id"), table_name="cost_analyses")
    op.drop_index(op.f("ix_cost_analyses_capacity_analysis_id"), table_name="cost_analyses")
    op.drop_index(op.f("ix_cost_analyses_architecture_id_requested_at_id"), table_name="cost_analyses")
    op.drop_table("cost_analyses")
