"""architecture metadata, lifecycle and restores

A project may now have many architectures. Adds each architecture's metadata (name, description:
existing architectures take their current revision's IR name), lifecycle (status, archived_at,
deleted_at, updated_by_user_id) and a listing index; names are unique among a project's live
architectures, ignoring case. Revisions record the earlier revision they restore
(restored_from_number, same architecture, earlier number). Revisions are looked up by
(architecture_id, number); the one-architecture-per-project constraints are dropped.

Downgrading is possible while no project has more than one architecture (deleted ones included);
it removes no data.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26 11:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKS = {
    "name_length": "char_length(name) BETWEEN 1 AND 100",
    "description_length": "char_length(description) <= 2000",
    "status_valid": "status IN ('active', 'archived')",
    "archived_at_matches_status": "(status = 'archived') = (archived_at IS NOT NULL)",
    "deleted_only_when_archived": "deleted_at IS NULL OR status = 'archived'",
}


def upgrade() -> None:
    op.add_column("architectures", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("architectures", sa.Column("description", sa.Text(), server_default="", nullable=False))
    op.add_column("architectures", sa.Column("status", sa.Text(), server_default="active", nullable=False))
    op.add_column("architectures", sa.Column("updated_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("architectures", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("architectures", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    # Existing architectures are named after their current revision's IR title.
    op.execute(
        """
        UPDATE architectures a
        SET name = left(coalesce(nullif(btrim(r.ir ->> 'name'), ''), 'Architecture'), 100)
        FROM architecture_revisions r
        WHERE r.architecture_id = a.id AND r.number = a.current_revision
        """
    )
    op.execute("UPDATE architectures SET name = 'Architecture' WHERE name IS NULL")
    op.alter_column("architectures", "name", nullable=False)
    op.drop_constraint(op.f("uq_architectures_project_id"), "architectures", type_="unique")
    op.create_index(
        "ix_architectures_project_id_created_at_id_live",
        "architectures",
        ["project_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_architectures_project_id_name_live",
        "architectures",
        ["project_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    for name, condition in _CHECKS.items():
        op.create_check_constraint(op.f(f"ck_architectures_{name}"), "architectures", condition)

    # Adding a column fires no row trigger: stored revisions are not rewritten.
    op.add_column("architecture_revisions", sa.Column("restored_from_number", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_architecture_revisions_restored_from_architecture_revisions",
        "architecture_revisions",
        "architecture_revisions",
        ["architecture_id", "restored_from_number"],
        ["architecture_id", "number"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_architecture_revisions_restores_an_earlier_revision"),
        "architecture_revisions",
        "restored_from_number IS NULL OR restored_from_number < number",
    )
    op.drop_constraint(
        op.f("uq_architecture_revisions_project_id_number"), "architecture_revisions", type_="unique"
    )


def downgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_architecture_revisions_project_id_number"),
        "architecture_revisions",
        ["project_id", "number"],
    )
    op.drop_constraint(
        op.f("ck_architecture_revisions_restores_an_earlier_revision"),
        "architecture_revisions",
        type_="check",
    )
    op.drop_constraint(
        "fk_architecture_revisions_restored_from_architecture_revisions",
        "architecture_revisions",
        type_="foreignkey",
    )
    op.drop_column("architecture_revisions", "restored_from_number")
    for name in _CHECKS:
        op.drop_constraint(op.f(f"ck_architectures_{name}"), "architectures", type_="check")
    op.drop_index(
        "uq_architectures_project_id_name_live",
        table_name="architectures",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "ix_architectures_project_id_created_at_id_live",
        table_name="architectures",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_unique_constraint(op.f("uq_architectures_project_id"), "architectures", ["project_id"])
    for column in ("deleted_at", "archived_at", "updated_by_user_id", "status", "description", "name"):
        op.drop_column("architectures", column)
