"""Simulations, their component outcomes and deltas. All three tables are append-only (a simulation is
stored once, finished; triggers refuse updates and deletes), belong to one project, and reference the
architecture revision they simulated through same-project foreign keys. Nothing here is a measurement:
rows hold the scenario snapshot, the overlay evaluated, and what the engines calculated from the
architecture as declared."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.simulations.values import AnalysisKind, Impact, SimulationStatus

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in SimulationStatus)))


class SimulationRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "simulations"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # the request (with the scenario snapshot), the requirements read, provider and currency
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    overlay: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    engine_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context_fingerprint: Mapped[str | None] = mapped_column(Text)
    scenario_fingerprint: Mapped[str | None] = mapped_column(Text)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    runs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    entries: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    trace: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unsupported: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the rows' same-project foreign keys
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_simulations_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_simulations_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        Index(None, "architecture_id", "requested_at", "id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("revision_content_hash ~ '^[0-9a-f]{64}$'", name="revision_content_hash_format"),
        CheckConstraint("label IS NULL OR char_length(label) BETWEEN 1 AND 100", name="label_length"),
        CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL AND result_fingerprint "
            "IS NOT NULL AND summary IS NOT NULL AND engine_set IS NOT NULL AND overlay IS NOT NULL)",
            name="finished_has_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="failed_has_error",
        ),
        CheckConstraint("jsonb_typeof(inputs) = 'object'", name="inputs_object"),
        CheckConstraint("jsonb_typeof(runs) = 'array'", name="runs_array"),
        CheckConstraint("jsonb_typeof(entries) = 'array'", name="entries_array"),
    )


class SimulationComponentRecord(UuidPrimaryKey, Base):
    """One component's outcome in a simulation; ``data`` is the whole outcome."""

    __tablename__ = "simulation_components"

    simulation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    unavailable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    impact: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("simulation_id", "node_id"),
        ForeignKeyConstraint(
            ["simulation_id", "project_id"],
            ["simulations.id", "simulations.project_id"],
            name="fk_simulation_components_simulation_simulations",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"impact IS NULL OR {in_values('impact', Impact)}", name="impact"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )


class SimulationDeltaRecord(UuidPrimaryKey, Base):
    """One comparison of a simulation, at its position in the canonical order."""

    __tablename__ = "simulation_deltas"

    simulation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    element_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    comparable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("simulation_id", "position"),
        UniqueConstraint("simulation_id", "analysis", "element_id", "metric"),
        ForeignKeyConstraint(
            ["simulation_id", "project_id"],
            ["simulations.id", "simulations.project_id"],
            name="fk_simulation_deltas_simulation_simulations",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(in_values("analysis", AnalysisKind), name="analysis"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
