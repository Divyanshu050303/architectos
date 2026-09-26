import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from core.domain.organizations.enums import Role
from core.domain.projects.entities import Project, ProjectAccess
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.policies import MAX_POLICY_ENTRIES, ArchitecturePolicy
from core.domain.projects.value_objects import CloudProvider, ProjectSettings

from .common import ApiModel, RequestModel


class ProjectSettingsModel(ApiModel):
    cloud_provider: CloudProvider | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3, examples=["USD"])] = "USD"

    def to_domain(self) -> ProjectSettings:
        return ProjectSettings.from_dict({"cloud_provider": self.cloud_provider, "currency": self.currency})

    @classmethod
    def from_domain(cls, settings: ProjectSettings) -> ProjectSettingsModel:
        return cls(cloud_provider=settings.cloud_provider, currency=settings.currency)


class ProjectSettingsInput(ProjectSettingsModel, RequestModel):
    """Settings in a request body: unknown keys are rejected."""


class ProjectResponse(ApiModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    description: str
    status: ProjectStatus
    settings: ProjectSettingsModel
    created_by_user_id: uuid.UUID | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    role: Role | None = Field(default=None, description="Your role in the project's organization.")

    @classmethod
    def from_project(cls, project: Project, *, role: Role | None = None) -> ProjectResponse:
        return cls(
            id=project.id,
            organization_id=project.organization_id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            status=project.status,
            settings=ProjectSettingsModel.from_domain(project.settings),
            created_by_user_id=project.created_by_user_id,
            archived_at=project.archived_at,
            created_at=project.created_at,
            updated_at=project.updated_at,
            role=role,
        )

    @classmethod
    def from_access(cls, access: ProjectAccess) -> ProjectResponse:
        return cls.from_project(access.project, role=access.membership.role)


class ProjectPage(ApiModel):
    projects: list[ProjectResponse]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at the end.")


class CreateProjectRequest(RequestModel):
    name: Annotated[str, Field(max_length=200, examples=["Food Delivery Platform"])]
    slug: Annotated[str | None, Field(max_length=100, examples=["food-delivery-platform"])] = None
    description: Annotated[str, Field(max_length=4000)] = ""
    settings: ProjectSettingsInput | None = None


class UpdateProjectRequest(RequestModel):
    """Name, description and settings only. Organization, slug and creator cannot change."""

    name: Annotated[str | None, Field(max_length=200)] = None
    description: Annotated[str | None, Field(max_length=4000)] = None
    settings: ProjectSettingsInput | None = None


def settings_or_none(model: ProjectSettingsInput | None) -> ProjectSettings | None:
    return model.to_domain() if model is not None else None


# --- architecture policy ------------------------------------------------------------------------

type PolicyNames = Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=MAX_POLICY_ENTRIES)]


class ArchitecturePolicyModel(ApiModel):
    """What validation enforces for this project. Empty lists and null constrain nothing."""

    allowed_technologies: PolicyNames = Field(
        default_factory=list, description="When not empty, every stated technology must be one of these."
    )
    prohibited_technologies: PolicyNames = Field(default_factory=list, examples=[["mongodb"]])
    allowed_regions: PolicyNames = Field(
        default_factory=list, description="When not empty, every stated region must be one of these."
    )
    require_tls: bool = Field(default=False, description="Every communicating connection is encrypted.")
    max_components: Annotated[int | None, Field(ge=1, le=1000)] = None

    def to_domain(self) -> ArchitecturePolicy:
        return ArchitecturePolicy.from_dict(self.model_dump(by_alias=False))

    @classmethod
    def from_domain(cls, policy: ArchitecturePolicy) -> ArchitecturePolicyModel:
        return cls(**policy.to_dict())


class ArchitecturePolicyRequest(ArchitecturePolicyModel, RequestModel):
    """The whole policy: fields left out are reset to their default (no constraint)."""


class ArchitecturePolicyResponse(ApiModel):
    project_id: uuid.UUID
    policy: ArchitecturePolicyModel
    updated_at: datetime

    @classmethod
    def from_access(cls, access: ProjectAccess) -> ArchitecturePolicyResponse:
        project = access.project
        return cls(
            project_id=project.id,
            policy=ArchitecturePolicyModel.from_domain(project.policy),
            updated_at=project.updated_at,
        )
