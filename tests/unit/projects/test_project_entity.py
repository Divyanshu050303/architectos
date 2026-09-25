import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.domain.projects.entities import NewProject, Project
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.errors import ProjectArchived, ProjectNotArchived, ProjectNotFound
from core.domain.projects.value_objects import CloudProvider, ProjectSettings

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ORG, USER = uuid.uuid7(), uuid.uuid7()


def active_project(**overrides: object) -> Project:
    fields: dict[str, object] = {
        "id": uuid.uuid7(),
        "organization_id": ORG,
        "name": "Food Delivery",
        "slug": "food-delivery",
        "description": "",
        "status": ProjectStatus.ACTIVE,
        "settings": ProjectSettings(),
        "created_by_user_id": USER,
        "archived_at": None,
        "deleted_at": None,
        "created_at": T0,
        "updated_at": T0,
    }
    return Project(**(fields | overrides))  # type: ignore[arg-type]


def test_creation_normalizes_and_derives_the_slug() -> None:
    new = NewProject.create(
        organization_id=ORG, created_by_user_id=USER, name="  Food   Delivery ", description=" Orders "
    )
    assert (new.name, new.slug, new.description) == ("Food Delivery", "food-delivery", "Orders")
    assert new.settings == ProjectSettings()


def test_an_explicit_slug_wins_over_the_derived_one() -> None:
    new = NewProject.create(organization_id=ORG, created_by_user_id=USER, name="Food Delivery", slug="orders")
    assert new.slug == "orders"


def test_changes_cover_name_description_and_settings_only() -> None:
    project = active_project()
    changed = project.with_changes(
        name=" Orders ", description="New", settings=ProjectSettings(cloud_provider=CloudProvider.GCP)
    )
    assert (changed.name, changed.description, changed.settings.cloud_provider) == (
        "Orders",
        "New",
        CloudProvider.GCP,
    )
    assert (changed.organization_id, changed.slug, changed.created_by_user_id) == (ORG, "food-delivery", USER)


def test_organization_and_slug_cannot_be_changed_through_the_entity() -> None:
    parameters = {f.name for f in dataclasses.fields(Project)}
    assert "organization_id" in parameters  # the field exists...
    import inspect  # noqa: PLC0415

    editable = set(inspect.signature(Project.with_changes).parameters) - {"self"}
    assert editable == {"name", "description", "settings"}  # ...but is not editable


def test_archive_is_idempotent_and_keeps_the_first_time() -> None:
    archived = active_project().archive(T0)
    again = archived.archive(T0 + timedelta(days=1))
    assert (archived.status, archived.archived_at) == (ProjectStatus.ARCHIVED, T0)
    assert again.archived_at == T0


def test_archived_projects_are_read_only() -> None:
    archived = active_project().archive(T0)
    for change in ({"name": "x"}, {"description": "x"}, {"settings": ProjectSettings()}):
        with pytest.raises(ProjectArchived):
            archived.with_changes(**change)


def test_restore_is_idempotent() -> None:
    restored = active_project().archive(T0).restore()
    assert (restored.status, restored.archived_at) == (ProjectStatus.ACTIVE, None)
    assert restored.restore() == restored


def test_deletion_requires_archiving_first() -> None:
    with pytest.raises(ProjectNotArchived):
        active_project().delete(T0)
    deleted = active_project().archive(T0).delete(T0)
    assert deleted.is_deleted


def test_deleted_projects_behave_as_missing() -> None:
    deleted = active_project().archive(T0).delete(T0)
    with pytest.raises(ProjectNotFound):
        deleted.restore()
    with pytest.raises(ProjectNotFound):
        deleted.archive(T0)
    with pytest.raises(ProjectNotFound):
        deleted.delete(T0)
    with pytest.raises(ProjectNotFound):
        deleted.with_changes(name="x")
