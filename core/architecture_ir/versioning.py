"""The IR *schema* version: the version of the representation format.

Not to be confused with an architecture's *revision* (a version of one architecture's content,
see core/domain/architecture): upgrading the schema never changes what an architecture says,
and a user editing an architecture never changes the schema.

Compatibility strategy:

- **Adding** an optional field, a node kind, a connection kind or a configuration property is
  backward compatible: old documents stay valid, and the schema version does not change.
- **Changing or removing** anything (a field's meaning, a unit, a name) is a new schema version,
  with an upgrade function from the previous one registered in ``UPGRADES``. Stored documents are
  upgraded when read, step by step (1 → 2 → 3), and never rewritten in place.
- An upgrade is pure and deterministic, keeps every identifier, and never changes what the
  architecture says; a value it has to introduce is marked with provenance ``schema_migration``.
- A document from a *newer* schema than this code knows is refused, never guessed at.
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any, Final

from .errors import ElementType, InvalidArchitecture, Violation

IR_SCHEMA_VERSION: Final = 1

type Upgrade = Callable[[dict[str, Any]], dict[str, Any]]

# UPGRADES[n] turns a version-n document into a version-(n + 1) document.
UPGRADES: dict[int, Upgrade] = {}


def _refuse(message: str) -> InvalidArchitecture:
    return InvalidArchitecture(
        [Violation("unsupported_schema_version", message, "schema_version", ElementType.ARCHITECTURE)]
    )


def schema_version_of(data: Mapping[str, Any]) -> int:
    version = data.get("schema_version")
    if version is None:
        raise InvalidArchitecture(
            [Violation("required", "schema_version is required.", "schema_version", ElementType.ARCHITECTURE)]
        )
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _refuse(f"schema_version must be a positive whole number, not {version!r}.")
    return version


def upgrade(
    data: Mapping[str, Any],
    upgrades: Mapping[int, Upgrade] | None = None,
    current: int = IR_SCHEMA_VERSION,
) -> tuple[dict[str, Any], int]:
    """``data`` in the ``current`` schema, and the version it was written in. The input is not
    modified. (``upgrades`` and ``current`` are parameters so the chain can be tested.)"""
    steps = UPGRADES if upgrades is None else upgrades
    original = schema_version_of(data)
    if original > current:
        raise _refuse(
            f"This architecture uses schema version {original}, newer than this version of "
            f"ArchitectOS understands ({current})."
        )
    if original == current:
        return dict(data), original  # nothing to do, and nothing is modified downstream
    try:
        document = copy.deepcopy(dict(data))
    except RecursionError:
        raise _refuse("This architecture is nested too deeply to be read.") from None
    version = original
    while version < current:
        step = steps.get(version)
        if step is None:
            raise _refuse(f"Schema version {version} can no longer be read.")
        document = step(document)
        version += 1
        document["schema_version"] = version
    return document, original
