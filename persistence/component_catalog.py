"""The component catalog's files: reviewed YAML specifications in the repository, read as untrusted
input into the read-only catalog.

Layout (``knowledge/components/``)::

    catalog.lock.json                               every published version and its content hash
    <category directory>/<entry>.yaml               the current version of <category directory>/<entry>
    <category directory>/history/<entry>@<n>.yaml   an older version, kept readable

- Files are parsed with a safe loader that also refuses aliases (no object construction, no
  expansion bombs), bounded in size and number, and must hold one mapping. A file's place must match
  the specification it holds (its id and, in history, its version).
- ``lock`` records new versions in the lock file and refuses to change or drop a recorded one: a
  published version is never rewritten (``make catalog-lock`` after adding a version).
"""

import json
import re
import sys
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from core.domain.components.entities import CATEGORIES
from core.domain.components.errors import InvalidCatalog, InvalidSpecification
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "knowledge" / "components"
LOCK_FILE = "catalog.lock.json"
HISTORY = "history"
ENTRY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
HISTORICAL = re.compile(r"^(?P<entry>[a-z0-9][a-z0-9_.-]{0,63})@(?P<version>[1-9][0-9]{0,2})$")
ALLOWED_AT_ROOT = frozenset({LOCK_FILE, "README.md"})
MAX_FILE_BYTES = 256_000
MAX_FILES = 2_000
TOP, ENTRY_DEPTH, HISTORY_DEPTH = 1, 2, 3  # path depths below the root


class _SafeLoader(yaml.SafeLoader):
    """YAML's safe loader, without aliases: a specification is plain data."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases are not allowed")
        return super().compose_node(parent, index)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_yaml(root: Path, path: Path) -> object:
    where = _relative(root, path)
    if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise InvalidCatalog(details={"reason": "unreadable_file", "file": where})
    try:
        return yaml.load(path.read_text("utf-8"), Loader=_SafeLoader)  # noqa: S506 - a safe loader
    except yaml.YAMLError, UnicodeDecodeError:
        raise InvalidCatalog(details={"reason": "unreadable_file", "file": where}) from None


def _specification(root: Path, path: Path, component_id: str, version: int | None) -> ComponentSpecification:
    where = _relative(root, path)
    try:
        spec = ComponentSpecification.from_dict(_read_yaml(root, path))
    except InvalidSpecification as error:
        fields = error.details.get("fields", []) if isinstance(error.details, dict) else []
        raise InvalidCatalog(
            details={"reason": "invalid_specification", "file": where, "fields": fields}
        ) from None
    if spec.id != component_id or (version is not None and spec.version != version):
        raise InvalidCatalog(details={"reason": "misplaced_specification", "file": where})
    return spec


def _files(root: Path) -> Iterator[Path]:
    for count, path in enumerate(sorted(root.rglob("*")), start=1):
        if count > MAX_FILES:
            raise InvalidCatalog(details={"reason": "too_large"})
        yield path


def read_specifications(root: Path) -> list[ComponentSpecification]:
    """Every version of every specification under ``root``, as its files state them."""
    directories = {c.directory for c in CATEGORIES.values()}
    current: dict[str, ComponentSpecification] = {}
    older: list[tuple[Path, str, int]] = []
    for path in _files(root):
        parts = path.relative_to(root).parts
        depth = len(parts)
        is_yaml = path.suffix == ".yaml" and path.is_file() and not path.is_symlink()
        if depth == TOP and (path.name in ALLOWED_AT_ROOT or (path.is_dir() and path.name in directories)):
            continue
        if depth == ENTRY_DEPTH and parts[1] == HISTORY and path.is_dir():
            continue
        if depth == ENTRY_DEPTH and is_yaml and ENTRY.fullmatch(path.stem):
            component_id = f"{parts[0]}/{path.stem}"
            current[component_id] = _specification(root, path, component_id, None)
            continue
        match = HISTORICAL.fullmatch(path.stem) if depth == HISTORY_DEPTH and parts[1] == HISTORY else None
        if match and is_yaml:
            older.append((path, f"{parts[0]}/{match['entry']}", int(match["version"])))
            continue
        raise InvalidCatalog(details={"reason": "unexpected_file", "file": _relative(root, path)})
    specifications = list(current.values())
    for path, component_id, version in older:
        latest = current.get(component_id)
        if latest is None or version >= latest.version:
            raise InvalidCatalog(details={"reason": "misplaced_specification", "file": _relative(root, path)})
        specifications.append(_specification(root, path, component_id, version))
    return specifications


def read_lock(root: Path) -> dict[str, str]:
    path = root / LOCK_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError, UnicodeDecodeError:
        raise InvalidCatalog(details={"reason": "unreadable_file", "file": LOCK_FILE}) from None
    versions = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(versions, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in versions.items()
    ):
        raise InvalidCatalog(details={"reason": "unreadable_file", "file": LOCK_FILE})
    return versions


def load_catalog(root: Path = DEFAULT_ROOT) -> ComponentCatalog:
    return ComponentCatalog.build(read_specifications(root), read_lock(root))


@cache
def default_catalog() -> ComponentCatalog:
    """The repository's catalog, read once per process (the files do not change while it runs)."""
    return load_catalog(DEFAULT_ROOT)


def lock(root: Path = DEFAULT_ROOT) -> list[str]:
    """Records the versions not yet in the lock; refuses to change or drop a recorded one. Returns
    the refs it added."""
    recorded = read_lock(root)
    specifications = read_specifications(root)
    found = {s.ref: s.content_hash for s in specifications}
    for ref, content_hash in sorted(recorded.items()):
        if ref not in found:
            raise InvalidCatalog(details={"reason": "published_version_removed", "ref": ref})
        if found[ref] != content_hash:
            raise InvalidCatalog(details={"reason": "changed_without_new_version", "ref": ref})
    ComponentCatalog.build(specifications, found)  # the whole catalog must load before it is recorded
    text = json.dumps({"versions": dict(sorted(found.items()))}, indent=2, sort_keys=True) + "\n"
    (root / LOCK_FILE).write_text(text, "utf-8")
    return sorted(set(found) - set(recorded))


if __name__ == "__main__":
    if sys.argv[1:] != ["lock"]:
        sys.exit("usage: python -m persistence.component_catalog lock")
    for ref in lock():
        sys.stdout.write(f"locked {ref}\n")
