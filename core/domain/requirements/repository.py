import uuid
from typing import Any, Protocol

from .analyses import NewRequirementAnalysis, RequirementAnalysis
from .entities import NewRequirement, Requirement, RequirementVersion, Revision
from .enums import RequirementStatus
from .queries import RequirementQuery
from .requirement_sets import NewRequirementSet, RequirementSet


class RequirementRepository(Protocol):
    """Every method is scoped by project: a requirement id alone never finds anything, so a
    requirement of another project (or tenant) is indistinguishable from a missing one.

    Writes assume the caller holds the project row lock (see Project.ensure_modifiable): that
    serializes number allocation and keeps archived projects frozen."""

    async def add(self, requirement: NewRequirement) -> Requirement:
        """Allocates the next number in the project, stores the requirement and its version 1.
        Raises CandidateAlreadyPromoted if its origin already has a live requirement (the
        surrounding transaction stays usable)."""
        ...

    async def get(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, for_update: bool = False
    ) -> Requirement | None:
        """A requirement of the project that is not deleted; ``for_update`` locks its row."""
        ...

    async def save(self, revision: Revision) -> Requirement:
        """Appends ``revision`` as version ``revision.requirement.version`` and makes it the current
        state; returns the stored requirement."""
        ...

    async def save_deleted(self, requirement: Requirement) -> None:
        """Persists the soft delete (``deleted_at``); versions are untouched."""
        ...

    async def list_for_project(self, project_id: uuid.UUID, query: RequirementQuery) -> list[Requirement]:
        """Live requirements, newest first, filtered in the database, at most ``query.limit`` rows."""
        ...

    async def list_by_status(
        self, project_id: uuid.UUID, statuses: frozenset[RequirementStatus], *, limit: int
    ) -> list[Requirement]:
        """Live requirements in the given statuses, by number, at most ``limit`` (for analysis)."""
        ...

    async def list_by_ids(self, project_id: uuid.UUID, requirement_ids: list[uuid.UUID]) -> list[Requirement]:
        """The live requirements of the project among ``requirement_ids`` (others are ignored)."""
        ...

    async def list_versions(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, after: int | None, limit: int
    ) -> list[RequirementVersion]:
        """Versions of a live requirement in ascending order, after version ``after``."""
        ...

    async def get_version(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, version: int
    ) -> RequirementVersion | None:
        """One version of a live requirement."""
        ...


class RequirementSetRepository(Protocol):
    """Scoped by project like requirements. Sets are append-only: there is no update or delete."""

    async def add(self, requirement_set: NewRequirementSet) -> RequirementSet:
        """Allocates the next set number in the project (caller holds the project row lock) and
        stores the set with its items and planning input."""
        ...

    async def get(self, project_id: uuid.UUID, set_id: uuid.UUID) -> RequirementSet | None:
        """The set with its pinned versions."""
        ...

    async def list_for_project(
        self, project_id: uuid.UUID, *, before_number: int | None, limit: int
    ) -> list[RequirementSet]:
        """Newest first, without items."""
        ...

    async def get_planning_input(
        self, project_id: uuid.UUID, set_id: uuid.UUID
    ) -> tuple[RequirementSet, dict[str, Any]] | None:
        """The set (without items) and its stored Architecture Planning Input, exactly as built at
        creation, in one query."""
        ...


class RequirementAnalysisRepository(Protocol):
    """Scoped by project. Append-only: there is no update or delete."""

    async def add(self, analysis: NewRequirementAnalysis) -> RequirementAnalysis: ...

    async def get(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> RequirementAnalysis | None: ...
