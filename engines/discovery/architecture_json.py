"""An Architecture IR document exported from ArchitectOS.

The document is read by the IR's own ``from_dict`` (schema upgrades and every structural check
included): a document the IR refuses is reported with its violations, never repaired. Each node
becomes an entity keyed by its own id, with its kind, technology, catalog component and
configuration values (secret paths redacted by the IR's own rule); each connection becomes a
reference from its source to its target, with its kind recorded on the source. The values are what
the document states — a file's claims, not the running system.
"""

from typing import Any

from core.architecture_ir.diff import is_secret_path
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.serialization import from_dict, to_dict
from core.domain.discovery.values import Severity, SourceType

from .adapters import Emitter, Extracted
from .loading import Document

EXTRACTOR = "architecture_json@1"
MAX_VIOLATIONS = 20


def _node(emit: Emitter, node: dict[str, Any], where: str) -> None:
    key = node["id"]
    if not emit.entity(key, f"{where}.id"):
        return
    emit.property(key, "kind", "kind", node["kind"], f"{where}.kind")
    technology = node.get("technology") or {}
    if technology.get("name"):
        emit.property(key, "technology", "technology.name", technology["name"], f"{where}.technology")
    if technology.get("version"):
        version = technology["version"]
        emit.property(key, "technology_version", "technology.version", version, f"{where}.technology")
    if node.get("component"):
        emit.property(key, "component", "component", node["component"], f"{where}.component")
    for name, value in (node.get("configuration") or {}).get("values", {}).items():
        path = f"configuration.{name}"
        emit.property(key, path, path, value, f"{where}.{path}", secret=is_secret_path(path))


class ArchitectureJsonAdapter:
    source_type = SourceType.ARCHITECTURE_JSON
    version = 1

    def detect(self, path: str, documents: tuple[Document, ...]) -> bool:
        if len(documents) != 1 or not isinstance(documents[0].data, dict):
            return False
        data = documents[0].data
        return "schema_version" in data and isinstance(data.get("nodes"), list)

    def extract(self, path: str, documents: tuple[Document, ...]) -> Extracted:
        document = documents[0]
        emit = Emitter(path, document, EXTRACTOR)
        try:
            ir = from_dict(document.data)
        except InvalidArchitecture as error:
            for violation in list(error.violations)[:MAX_VIOLATIONS]:
                emit.diagnose("invalid_architecture", Severity.ERROR, violation.message, None)
            return emit.findings, emit.diagnostics, None
        data = to_dict(ir)
        for index, node in enumerate(data["nodes"]):
            _node(emit, node, f"nodes[{index}]")
        for index, connection in enumerate(data["connections"]):
            where, source = f"connections[{index}]", connection["source_id"]
            identifier = connection["id"]
            emit.reference(source, connection["target_id"], f"{where}.target_id", f"connections.{identifier}")
            emit.property(
                source, f"connection.{identifier}.kind", "kind", connection["kind"], f"{where}.kind"
            )
        return emit.findings, emit.diagnostics, str(data["schema_version"])
