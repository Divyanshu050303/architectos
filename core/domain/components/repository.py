"""The component catalog: every version of every specification, read-only, with the rules that keep
historical analyses reproducible.

- **Versions.** Each component has versions 1..N, without gaps; the latest is its *current*
  specification. Older versions stay readable (an analysis that used ``databases/postgresql@1``
  can always be re-read against it), and so do deprecated entries.
- **The lock.** Every published version is recorded with its content hash
  (``knowledge/components/catalog.lock.json``). A version whose content differs from its recorded
  hash was edited in place — refused: a change is a new version. A recorded version that is missing
  was removed — refused. A version not yet recorded is refused too, until it is locked
  (``make catalog-lock``), so nothing reaches an analysis without being reviewed and recorded.
- **Identity.** ``fingerprint`` identifies the whole catalog (every ref and hash): an analysis can
  record it next to the refs it used.

Lookups are by catalog id (``Node.component``), never by names or aliases.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Self

from .entities import CATEGORIES, Category, SupportStatus
from .errors import ComponentNotFound, InvalidCatalog
from .specifications import ComponentSpecification

MAX_COMPONENTS = 1000
MAX_VERSIONS = 100


@dataclass(frozen=True, slots=True)
class CategorySummary:
    category: Category
    components: int  # current entries in the category
    by_status: Mapping[SupportStatus, int]


class ComponentCatalog:
    def __init__(self, versions: Mapping[str, tuple[ComponentSpecification, ...]], fingerprint: str) -> None:
        self._versions = dict(versions)
        self._fingerprint = fingerprint

    @classmethod
    def build(cls, specifications: Iterable[ComponentSpecification], lock: Mapping[str, str]) -> Self:
        """The catalog from every version of every specification, checked against the lock."""
        by_id: dict[str, dict[int, ComponentSpecification]] = {}
        for spec in specifications:
            versions = by_id.setdefault(spec.id, {})
            if spec.version in versions:
                raise InvalidCatalog(details={"reason": "duplicate_version", "ref": spec.ref})
            versions[spec.version] = spec
        if len(by_id) > MAX_COMPONENTS or any(len(v) > MAX_VERSIONS for v in by_id.values()):
            raise InvalidCatalog(details={"reason": "too_large"})
        ordered: dict[str, tuple[ComponentSpecification, ...]] = {}
        for component_id, versions in sorted(by_id.items()):
            if sorted(versions) != list(range(1, len(versions) + 1)):
                raise InvalidCatalog(details={"reason": "version_gap", "component": component_id})
            ordered[component_id] = tuple(versions[n] for n in sorted(versions))
        found = {s.ref: s.content_hash for versions in ordered.values() for s in versions}
        for ref, content_hash in sorted(found.items()):
            if ref not in lock:
                raise InvalidCatalog(details={"reason": "not_locked", "ref": ref})
            if lock[ref] != content_hash:
                raise InvalidCatalog(details={"reason": "changed_without_new_version", "ref": ref})
        removed = sorted(set(lock) - set(found))
        if removed:
            raise InvalidCatalog(details={"reason": "published_version_removed", "ref": removed[0]})
        for chain in ordered.values():
            successor = chain[-1].replaced_by
            if successor is not None and successor not in ordered:
                raise InvalidCatalog(details={"reason": "unknown_successor", "ref": chain[-1].ref})
        canonical = json.dumps(sorted(found.items()), separators=(",", ":"))
        return cls(ordered, hashlib.sha256(canonical.encode()).hexdigest())

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def categories(self) -> tuple[CategorySummary, ...]:
        summaries = []
        for category in CATEGORIES.values():
            current = [v[-1] for v in self._versions.values() if v[-1].category == category.id]
            counts = {s: sum(1 for c in current if c.support_status is s) for s in SupportStatus}
            summaries.append(CategorySummary(category, len(current), counts))
        return tuple(summaries)

    def list(
        self, *, category: str | None = None, status: SupportStatus | None = None
    ) -> tuple[ComponentSpecification, ...]:
        """The current specification of each component, by id."""
        return tuple(
            versions[-1]
            for versions in self._versions.values()
            if (category is None or versions[-1].category == category)
            and (status is None or versions[-1].support_status is status)
        )

    def get(self, component_id: str, version: int | None = None) -> ComponentSpecification:
        """The current specification, or the exact version asked for."""
        versions = self._versions.get(component_id) if isinstance(component_id, str) else None
        if versions is None:
            raise ComponentNotFound(details={"component": component_id})
        if version is None:
            return versions[-1]
        if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= len(versions):
            raise ComponentNotFound(details={"component": component_id, "version": version})
        return versions[version - 1]

    def history(self, component_id: str) -> tuple[ComponentSpecification, ...]:
        """Every version of a component, oldest first."""
        self.get(component_id)
        return self._versions[component_id]

    def is_current(self, spec: ComponentSpecification) -> bool:
        """Whether an analysis that used ``spec`` used the current version (or an older one)."""
        return self.get(spec.id).ref == spec.ref

    def __len__(self) -> int:
        return len(self._versions)
