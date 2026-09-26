"""pricing snapshots

An organization's price lists for the cost engine: immutable snapshots (name, content hash, record
count, creator) and their records (provider, service, SKU, region, currency, effective date copied
out for lookup; the whole record, with its unit, model, price or tiers, source and retrieval time,
in ``data``). Both tables are append-only (triggers, with the guard function of requirement sets,
0004): a new price list is a new snapshot, so the prices behind a historical analysis never change.
Records reach their snapshot through a same-organization foreign key.

Downgrading removes both tables and every price list with them.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-26 20:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

TABLES = ("pricing_snapshots", "pricing_records")

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pricing_snapshots",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_pricing_snapshots_content_hash_format")
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name=op.f("ck_pricing_snapshots_name_length")
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 500",
            name=op.f("ck_pricing_snapshots_description_length"),
        ),
        sa.CheckConstraint(
            "record_count BETWEEN 1 AND 10000", name=op.f("ck_pricing_snapshots_record_count_range")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_pricing_snapshots_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_snapshots")),
        sa.UniqueConstraint("id", "organization_id", name=op.f("uq_pricing_snapshots_id_organization_id")),
    )
    op.create_index(
        op.f("ix_pricing_snapshots_organization_id_created_at_id"),
        "pricing_snapshots",
        ["organization_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "pricing_records",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_pricing_records_currency_format")),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name=op.f("ck_pricing_records_data_object")),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["pricing_snapshots.id", "pricing_snapshots.organization_id"],
            name="fk_pricing_records_snapshot_pricing_snapshots",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_records")),
        sa.UniqueConstraint(
            "snapshot_id", "record_id", name=op.f("uq_pricing_records_snapshot_id_record_id")
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
    op.drop_table("pricing_records")
    op.drop_index(op.f("ix_pricing_snapshots_organization_id_created_at_id"), table_name="pricing_snapshots")
    op.drop_table("pricing_snapshots")
