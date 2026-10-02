"""Identity mappings: a person's statement that a baseline node and a discovered entity are the same
thing — the evidence of continuity a rename leaves behind, which no name can provide.

A mapping belongs to one architecture, names a baseline node id and a discovery key, and records who
confirmed it and when (with an optional note). It is used only for matching; it never changes the
architecture or a discovery run. Mappings are history: a newer mapping for the same baseline node
replaces the older one for matching, and a retracted mapping maps to nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.domain.discovery.values import KEY

from .errors import InvalidDriftRequest

MAX_NOTE = 2000


@dataclass(frozen=True, slots=True)
class IdentityMapping:
    architecture_id: uuid.UUID
    baseline_id: str  # a node id of the architecture
    discovered_key: str | None  # a discovery entity key; None: the mapping is retracted
    confirmed_by_user_id: uuid.UUID
    confirmed_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_id, str) or not KEY.fullmatch(self.baseline_id):
            raise InvalidDriftRequest(details={"field": "baseline_id", "reason": "invalid_identifier"})
        key = self.discovered_key
        if key is not None and (not isinstance(key, str) or not KEY.fullmatch(key)):
            raise InvalidDriftRequest(details={"field": "discovered_key", "reason": "invalid_identifier"})
        note = self.note
        if note is not None and not (isinstance(note, str) and note.strip() and len(note) <= MAX_NOTE):
            raise InvalidDriftRequest(details={"field": "note", "reason": "invalid_text"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_id": str(self.architecture_id),
            "baseline_id": self.baseline_id,
            "discovered_key": self.discovered_key,
            "confirmed_by_user_id": str(self.confirmed_by_user_id),
            "confirmed_at": self.confirmed_at.isoformat(),
            "note": self.note,
        }


def current(mappings: tuple[IdentityMapping, ...]) -> dict[str, str]:
    """The mapping in force per baseline node (the latest confirmed; retracted ones map to nothing)."""
    latest: dict[str, IdentityMapping] = {}
    for mapping in sorted(mappings, key=lambda m: m.confirmed_at):
        latest[mapping.baseline_id] = mapping
    return {b: m.discovered_key for b, m in sorted(latest.items()) if m.discovered_key is not None}
