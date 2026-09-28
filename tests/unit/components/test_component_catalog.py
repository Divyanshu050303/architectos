"""The component catalog (ARCH-COMP-001, phase 2): every version of every specification, read-only;
lookups by catalog id and exact version; a lock that refuses edited, removed or unrecorded versions;
deprecated entries still readable; a fingerprint of the whole catalog."""

from typing import Any

import pytest

from core.domain.components.entities import SupportStatus
from core.domain.components.errors import ComponentNotFound, InvalidCatalog
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification
from tests.unit.components.test_component_specifications import spec


def version(n: int, **overrides: Any) -> ComponentSpecification:
    return ComponentSpecification.from_dict(spec(version=n, description=f"Version {n}.", **overrides))


def planned(component_id: str, category: str, kinds: list[str]) -> ComponentSpecification:
    data = {
        k: v for k, v in spec().items() if k in {"version", "name", "technology", "description", "provider"}
    }
    return ComponentSpecification.from_dict(
        data | {"id": component_id, "category": category, "node_kinds": kinds, "support_status": "planned"}
    )


def locked(*specs: ComponentSpecification) -> dict[str, str]:
    return {s.ref: s.content_hash for s in specs}


def catalog(*specs: ComponentSpecification) -> ComponentCatalog:
    return ComponentCatalog.build(specs, locked(*specs))


def reason(specs: tuple[ComponentSpecification, ...], lock: dict[str, str]) -> str:
    with pytest.raises(InvalidCatalog) as error:
        ComponentCatalog.build(specs, lock)
    return str(error.value.details["reason"])


def test_the_current_version_and_every_older_one_are_readable() -> None:
    one, two = version(1), version(2)
    found = catalog(two, one)
    assert found.get("databases/example-sql") == two
    assert found.get("databases/example-sql", 1) == one  # an analysis of version 1 can always be re-read
    assert found.history("databases/example-sql") == (one, two)
    assert (found.is_current(two), found.is_current(one)) == (True, False)
    assert len(found) == 1


def test_lookups_are_by_catalog_id_and_exact_version() -> None:
    found = catalog(version(1))
    for component_id, asked in [
        ("databases/examplesql", None),  # an alias is not an id
        ("Example SQL", None),  # nor is a name
        ("databases/example-sql", 2),
        ("databases/example-sql", 0),
        ("databases/example-sql", True),
    ]:
        with pytest.raises(ComponentNotFound):
            found.get(component_id, asked)


def test_a_published_version_is_never_rewritten_or_removed() -> None:
    one, two = version(1), version(2)
    edited = version(1, name="Example SQL, edited")
    assert reason((edited,), locked(one)) == "changed_without_new_version"  # a change is a new version
    assert reason((one,), locked(one, two)) == "published_version_removed"
    assert reason((one, two), locked(one)) == "not_locked"  # recorded before it is used
    assert reason((two,), locked(two)) == "version_gap"
    assert reason((one, one), locked(one)) == "duplicate_version"


def test_deprecated_entries_stay_readable_and_name_a_known_successor() -> None:
    successor = planned("databases/next-sql", "database", ["database"])
    old = version(1, support_status="deprecated", replaced_by="databases/next-sql")
    found = catalog(old, successor)
    assert found.get("databases/example-sql").support_status is SupportStatus.DEPRECATED
    assert reason((old,), locked(old)) == "unknown_successor"


def test_listing_and_categories_count_current_entries_by_status() -> None:
    found = catalog(
        version(1),
        version(2),
        planned("databases/other-sql", "database", ["database"]),
        planned("messaging/example-queue", "messaging", ["queue"]),
    )
    assert [s.ref for s in found.list()] == [
        "databases/example-sql@2",
        "databases/other-sql@1",
        "messaging/example-queue@1",
    ]
    assert [s.id for s in found.list(category="messaging")] == ["messaging/example-queue"]
    assert [s.id for s in found.list(status=SupportStatus.SUPPORTED)] == ["databases/example-sql"]
    summaries = {s.category.id: s for s in found.categories()}
    assert summaries["database"].components == 2
    assert summaries["database"].by_status[SupportStatus.PLANNED] == 1
    assert summaries["storage"].components == 0  # every category, even an empty one


def test_the_fingerprint_identifies_the_whole_catalog() -> None:
    first = catalog(version(1))
    assert catalog(version(1)).fingerprint == first.fingerprint
    assert catalog(version(1), version(2)).fingerprint != first.fingerprint
