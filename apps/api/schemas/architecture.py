"""Architecture API contract.

The envelope follows the API's conventions (camelCase); the architecture itself travels as the
canonical Architecture IR document (``ir``), exactly as core/architecture_ir writes and reads it
and as published in core/schemas/architecture.schema.json. The IR is a versioned format of its
own (snake_case, ``schema_version``), so it is never reshaped at this boundary.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from core.architecture_ir.commands import (
    MAX_COMMANDS,
    AddConnection,
    AddNode,
    ChangeReplicas,
    Command,
    RemoveConnections,
    RemoveNodes,
    RenameNode,
    UpdateConfiguration,
)
from core.architecture_ir.diff import ArchitectureDiff, ElementChange, FieldChange
from core.architecture_ir.serialization import connection_from_dict, node_from_dict, property_values, to_dict
from core.domain.architecture.entities import Architecture, ArchitectureLayout, Position, RevisionSummary
from core.domain.architecture.versions import MAX_REASON_LENGTH, ArchitectureRevision

from .common import ApiModel, RequestModel

IR_DESCRIPTION = (
    "The architecture as a canonical Architecture IR document (snake_case, with schema_version); "
    "see core/schemas/architecture.schema.json."
)
Reason = Annotated[str | None, Field(max_length=MAX_REASON_LENGTH, description="Why the change.")]
ElementId = Annotated[str, Field(min_length=1, max_length=128)]


# --- layout ----------------------------------------------------------------------------------------


class PositionModel(ApiModel):
    x: float
    y: float


class LayoutModel(ApiModel):
    positions: dict[str, PositionModel] = Field(description="Where each node is drawn, by node id.")
    updated_at: datetime | None

    @classmethod
    def of(cls, layout: ArchitectureLayout, node_ids: frozenset[str] | None = None) -> LayoutModel:
        return cls(
            positions={
                node_id: PositionModel(x=p.x, y=p.y)
                for node_id, p in layout.positions.items()
                if node_ids is None or node_id in node_ids
            },
            updated_at=layout.updated_at,
        )


class SaveLayoutRequest(RequestModel):
    positions: dict[str, PositionModel] = Field(description="Every drawn node's position, by node id.")

    def to_domain(self) -> dict[str, Position]:
        return {node_id: Position(p.x, p.y) for node_id, p in self.positions.items()}


# --- revisions -------------------------------------------------------------------------------------


class RevisionSummaryModel(ApiModel):
    version: int = Field(description="The revision number: 1, 2, 3, …")
    parent_version: int | None
    source: str = Field(description="user, ai, discovery, import or system")
    summary: str = Field(description="What changed, e.g. '1 node added (Cache); 2 nodes modified.'")
    reason: str | None
    content_hash: str = Field(description="SHA-256 of the canonical IR: equal architectures, equal hash.")
    ir_schema_version: int
    requirement_set_id: uuid.UUID | None = Field(description="The requirement set it was designed against.")
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    @staticmethod
    def fields_of(revision: ArchitectureRevision | RevisionSummary) -> dict[str, Any]:
        return {
            "version": revision.number,
            "parent_version": revision.parent_number,
            "source": revision.source.value,
            "summary": revision.summary,
            "reason": revision.reason,
            "content_hash": revision.content_hash,
            "ir_schema_version": revision.ir_schema_version,
            "requirement_set_id": revision.requirement_set_id,
            "created_by_user_id": revision.created_by_user_id,
            "created_at": revision.created_at,
        }

    @classmethod
    def of(cls, revision: ArchitectureRevision | RevisionSummary) -> RevisionSummaryModel:
        return cls(**cls.fields_of(revision))


class VersionPage(ApiModel):
    versions: list[RevisionSummaryModel] = Field(description="Newest first.")
    next_cursor: str | None


class ArchitectureResponse(RevisionSummaryModel):
    id: uuid.UUID = Field(description="The architecture's id (stable across revisions).")
    project_id: uuid.UUID
    current_version: int
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    layout: LayoutModel

    @staticmethod
    def fields_for(
        architecture: Architecture, revision: ArchitectureRevision, layout: ArchitectureLayout
    ) -> dict[str, Any]:
        node_ids = frozenset(n.id for n in revision.ir.nodes)
        return RevisionSummaryModel.fields_of(revision) | {
            "id": architecture.id,
            "project_id": architecture.project_id,
            "current_version": architecture.current_revision,
            "ir": to_dict(revision.ir),
            "layout": LayoutModel.of(layout, node_ids),
        }

    @classmethod
    def build(
        cls, architecture: Architecture, revision: ArchitectureRevision, layout: ArchitectureLayout
    ) -> ArchitectureResponse:
        return cls(**cls.fields_for(architecture, revision, layout))


class CreateArchitectureRequest(RequestModel):
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    source: Literal["user", "import"] = Field(
        default="user", description="user: designed here; import: brought in from a file or another tool."
    )
    reason: Reason = None
    requirement_set_id: uuid.UUID | None = Field(
        default=None, description="The requirement set this architecture is designed against."
    )


# --- edits -----------------------------------------------------------------------------------------


class AddNodeCommand(RequestModel):
    type: Literal["add_node"]
    node: dict[str, Any] = Field(description="A node in IR form.")

    def to_domain(self) -> Command:
        return AddNode(node_from_dict(self.node))


class RemoveNodesCommand(RequestModel):
    type: Literal["remove_nodes"]
    node_ids: list[ElementId] = Field(min_length=1, max_length=1000)

    def to_domain(self) -> Command:
        return RemoveNodes(tuple(self.node_ids))


class AddConnectionCommand(RequestModel):
    type: Literal["add_connection"]
    connection: dict[str, Any] = Field(description="A connection in IR form.")

    def to_domain(self) -> Command:
        return AddConnection(connection_from_dict(self.connection))


class RemoveConnectionsCommand(RequestModel):
    type: Literal["remove_connections"]
    connection_ids: list[ElementId] = Field(min_length=1, max_length=1000)

    def to_domain(self) -> Command:
        return RemoveConnections(tuple(self.connection_ids))


class RenameNodeCommand(RequestModel):
    type: Literal["rename_node"]
    node_id: ElementId
    name: str = Field(max_length=200)

    def to_domain(self) -> Command:
        return RenameNode(self.node_id, self.name)


class UpdateConfigurationCommand(RequestModel):
    type: Literal["update_configuration"]
    node_id: ElementId
    values: dict[str, Any] = Field(description="Known properties to set; null clears one.")

    def to_domain(self) -> Command:
        return UpdateConfiguration(self.node_id, property_values(self.values))


class ChangeReplicasCommand(RequestModel):
    type: Literal["change_replicas"]
    node_id: ElementId
    replicas: int

    def to_domain(self) -> Command:
        return ChangeReplicas(self.node_id, self.replicas)


CommandModel = Annotated[
    AddNodeCommand
    | RemoveNodesCommand
    | AddConnectionCommand
    | RemoveConnectionsCommand
    | RenameNodeCommand
    | UpdateConfigurationCommand
    | ChangeReplicasCommand,
    Field(discriminator="type"),
]


class EditArchitectureRequest(RequestModel):
    base_version: int = Field(ge=1, description="The revision the edits were made on; it must be current.")
    commands: list[CommandModel] = Field(min_length=1, max_length=MAX_COMMANDS)
    reason: Reason = None


# --- comparison ------------------------------------------------------------------------------------


class FieldChangeModel(ApiModel):
    field: str = Field(description="An IR field path, e.g. configuration.replicas.")
    before: Any
    after: Any
    category: str

    @classmethod
    def of(cls, change: FieldChange) -> FieldChangeModel:
        return cls(field=change.field, before=change.before, after=change.after, category=change.category)


class ElementChangeModel(ApiModel):
    element: str
    element_id: str
    change: str = Field(description="added, removed or modified")
    label: str | None
    kind: str | None
    categories: list[str]
    fields: list[FieldChangeModel]

    @classmethod
    def of(cls, change: ElementChange) -> ElementChangeModel:
        return cls(
            element=change.element.value,
            element_id=change.element_id,
            change=change.change.value,
            label=change.label,
            kind=change.kind,
            categories=sorted(change.categories),
            fields=[FieldChangeModel.of(f) for f in change.fields],
        )


class DiffModel(ApiModel):
    summary: str
    architecture: list[FieldChangeModel]
    nodes: list[ElementChangeModel]
    connections: list[ElementChangeModel]
    assumptions: list[ElementChangeModel]
    decisions: list[ElementChangeModel]

    @staticmethod
    def fields_of(diff: ArchitectureDiff) -> dict[str, Any]:
        return {
            "summary": diff.summary(),
            "architecture": [FieldChangeModel.of(f) for f in diff.architecture],
            "nodes": [ElementChangeModel.of(c) for c in diff.nodes],
            "connections": [ElementChangeModel.of(c) for c in diff.connections],
            "assumptions": [ElementChangeModel.of(c) for c in diff.assumptions],
            "decisions": [ElementChangeModel.of(c) for c in diff.decisions],
        }

    @classmethod
    def of(cls, diff: ArchitectureDiff) -> DiffModel:
        return cls(**cls.fields_of(diff))


class EditArchitectureResponse(ArchitectureResponse):
    changes: DiffModel = Field(description="What this revision changed from its parent.")


class ComparisonSide(ApiModel):
    version: int
    content_hash: str
    created_at: datetime

    @classmethod
    def of(cls, revision: ArchitectureRevision) -> ComparisonSide:
        return cls(
            version=revision.number, content_hash=revision.content_hash, created_at=revision.created_at
        )


class ArchitectureComparison(DiffModel):
    from_: ComparisonSide = Field(alias="from")
    to: ComparisonSide
    capacity: None = Field(default=None, description="Filled by the capacity engine; null until it exists.")
    cost: None = Field(default=None, description="Filled by the cost engine; null until it exists.")

    @classmethod
    def compare(
        cls, before: ArchitectureRevision, after: ArchitectureRevision, diff: ArchitectureDiff
    ) -> ArchitectureComparison:
        return cls(**DiffModel.fields_of(diff), from_=ComparisonSide.of(before), to=ComparisonSide.of(after))
