"""Terraform JSON: configuration in JSON syntax (``.tf.json``), or the output of ``terraform show
-json`` (a state or a plan). Native HCL is not read (reported by ``adapters``); nothing is ever
planned, applied, evaluated or interpolated.

- **Configuration**: each ``resource`` and ``data`` block becomes an entity (``terraform:<address>``)
  with its scalar attributes as written — an interpolation (``${aws_db_instance.main.address}``)
  stays text, and the addresses it names are explicit references, as are ``depends_on`` entries.
  ``module`` calls (their source is not supplied), provisioners and connection blocks are reported
  as unsupported.
- **``show -json``**: each resource of ``values`` (or ``planned_values``), in the root module and
  child modules, becomes an entity with its scalar attribute values; attributes Terraform marks
  sensitive are redacted; the configuration's expression references are explicit references.

Attributes whose names look like secrets are always redacted.
"""

import re
from typing import Any

from core.domain.discovery.values import Severity, SourceType

from .adapters import Emitter, Extracted, artifact_key
from .loading import Document, walk

EXTRACTOR = "terraform_json@1"
CONFIGURATION = frozenset(
    {"resource", "data", "module", "provider", "terraform", "variable", "output", "locals"}
)
SHOWN = frozenset({"values", "planned_values", "configuration", "prior_state"})
META = frozenset({"count", "for_each", "depends_on", "lifecycle", "provider", "provisioner", "connection"})
INTERPOLATION = re.compile(r"\$\{([^}]*)\}")
ADDRESS = re.compile(r"\b((?:data\.)?[a-z][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_-]*)")
INDEX = re.compile(r"\[\"?([A-Za-z0-9_.-]+)\"?\]")
MAX_MODULE_DEPTH = 8
NOT_RESOURCES = ("var.", "local.", "module.", "path.", "count.", "each.", "self.", "terraform.")


def address_key(address: str) -> str:
    """``terraform:<address>``, with ``[0]`` and ``["a"]`` written ``.0`` and ``.a``."""
    return "terraform:" + INDEX.sub(r".\1", address)


def _resource_address(target: str) -> bool:
    return target.startswith("data.") or not target.startswith(NOT_RESOURCES)


def references(value: Any) -> list[str]:
    """The resource addresses an expression names inside ``${...}``."""
    found: list[str] = []
    for _, item in walk(value):
        if isinstance(item, str):
            for expression in INTERPOLATION.findall(item):
                found += [a for a in ADDRESS.findall(expression) if _resource_address(a)]
    return found


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _name(attribute: str) -> str:
    """An attribute as a finding's property name (a lower-case code)."""
    cleaned = re.sub(r"[^a-z0-9_.-]", "_", attribute.lower()) or "attribute"
    return (cleaned if cleaned[0].isalpha() else f"a_{cleaned}")[:64]


def _blocks(value: Any) -> list[tuple[str, dict[str, Any], str]]:
    """(name, body, path) of a ``{name: body}`` block, or a list of such objects."""
    groups = value if isinstance(value, list) else [value]
    found: list[tuple[str, dict[str, Any], str]] = []
    for index, group in enumerate(groups):
        for name, body in _mapping(group).items():
            path = f"[{index}].{name}" if isinstance(value, list) else str(name)
            for item in body if isinstance(body, list) else [body]:
                if isinstance(item, dict):
                    found.append((str(name), item, path))
    return found


class _Configuration:
    def __init__(self, emit: Emitter, data: dict[str, Any]) -> None:
        self.emit, self.data = emit, data

    def read(self) -> None:
        for mode, prefix in (("resource", ""), ("data", "data.")):
            for kind, names, path in _blocks(self.data.get(mode)):
                for name, body, inner in _blocks(names):
                    self.resource(f"{prefix}{kind}.{name}", kind, body, f"{mode}.{path}.{inner}")
        for name, _body, path in _blocks(self.data.get("module")):
            message = f"Module {name}: its source is not supplied, so it is not expanded."
            self.emit.unsupported(
                artifact_key(self.emit.path), f"module.{path}", "module_not_expanded", message
            )
        ignored = sorted(set(self.data) & {"provider", "variable", "output", "locals", "terraform"})
        if ignored:
            message = f"Blocks not read as architecture: {', '.join(ignored)}."
            self.emit.diagnose("not_interpreted", Severity.INFO, message, None)

    def resource(self, address: str, kind: str, body: dict[str, Any], path: str) -> None:
        key = address_key(address)
        if not self.emit.entity(key, path):
            return
        self.emit.property(key, "resource_type", "type", kind, path)
        for name, value in body.items():
            where = f"{path}.{name}"
            if name in {"provisioner", "connection"}:
                message = f"{address}: its {name} block is never run or read."
                self.emit.unsupported(key, where, f"{name}_not_read", message)
            elif name == "depends_on":
                for target in value if isinstance(value, list) else []:
                    if isinstance(target, str):
                        self.emit.reference(key, target, where, "depends_on")
                continue
            elif name in {"count", "for_each"}:
                self.emit.property(key, name, name, value if isinstance(value, str | int) else None, where)
            elif isinstance(value, str | int | float | bool) and name not in META:
                self.emit.property(key, _name(name), name, value, where)
            for target in references(value):
                self.emit.reference(key, target, where, name)


class _Shown:
    def __init__(self, emit: Emitter, data: dict[str, Any]) -> None:
        self.emit, self.data = emit, data
        self.keys: dict[str, str] = {}  # configuration address (no module, no index) -> entity key

    def read(self) -> None:
        values = "values" if isinstance(self.data.get("values"), dict) else "planned_values"
        self.module(_mapping(_mapping(self.data.get(values)).get("root_module")), f"{values}.root_module", 0)
        self.expressions(_mapping(_mapping(self.data.get("configuration")).get("root_module")))
        if "resource_changes" in self.data:
            message = "Planned changes (resource_changes) are not read; the planned values are."
            self.emit.diagnose("not_interpreted", Severity.INFO, message, "resource_changes")

    def module(self, module: dict[str, Any], path: str, depth: int) -> None:
        if depth > MAX_MODULE_DEPTH:
            message = f"Modules nested deeper than {MAX_MODULE_DEPTH} levels are not read."
            self.emit.unsupported(artifact_key(self.emit.path), path, "too_deep", message)
            return
        for index, resource in enumerate(module.get("resources") or []):
            if isinstance(resource, dict) and isinstance(resource.get("address"), str):
                self.resource(resource, f"{path}.resources[{index}]")
        for index, nested in enumerate(module.get("child_modules") or []):
            self.module(_mapping(nested), f"{path}.child_modules[{index}]", depth + 1)

    def resource(self, resource: dict[str, Any], path: str) -> None:
        key = address_key(resource["address"])
        if not self.emit.entity(key, f"{path}.address"):
            return
        kind = resource.get("type")
        self.emit.property(key, "resource_type", "type", kind, f"{path}.type")
        mode = "data." if resource.get("mode") == "data" else ""
        self.keys.setdefault(f"{mode}{kind}.{resource.get('name')}", key)
        sensitive = _mapping(resource.get("sensitive_values"))
        for name, value in _mapping(resource.get("values")).items():
            if isinstance(value, str | int | float | bool):
                where = f"{path}.values.{name}"
                self.emit.property(key, _name(name), name, value, where, secret=sensitive.get(name) is True)

    def expressions(self, module: dict[str, Any]) -> None:
        for index, resource in enumerate(module.get("resources") or []):
            body = _mapping(resource)
            address = body.get("address")
            key = self.keys.get(address) if isinstance(address, str) else None
            if key is None:
                continue
            path = f"configuration.root_module.resources[{index}]"
            for pointer, value in walk(_mapping(body.get("expressions")), f"{path}.expressions"):
                if pointer.endswith(".references") and isinstance(value, list):
                    attribute = pointer.removeprefix(f"{path}.expressions.").split(".", 1)[0]
                    for target in value:
                        if isinstance(target, str) and _resource_address(target):
                            self.emit.reference(key, target, pointer, attribute)
            for target in body.get("depends_on") or []:
                if isinstance(target, str):
                    self.emit.reference(key, target, f"{path}.depends_on", "depends_on")


class TerraformJsonAdapter:
    source_type = SourceType.TERRAFORM_JSON
    version = 1

    def detect(self, path: str, documents: tuple[Document, ...]) -> bool:
        if len(documents) != 1 or not isinstance(documents[0].data, dict):
            return False
        keys = set(documents[0].data)
        configuration = bool(keys & {"resource", "data", "module"}) and keys <= CONFIGURATION
        return configuration or ("format_version" in keys and bool(keys & SHOWN))

    def extract(self, path: str, documents: tuple[Document, ...]) -> Extracted:
        document = documents[0]
        emit = Emitter(path, document, EXTRACTOR)
        data = _mapping(document.data)
        if "format_version" in data:
            _Shown(emit, data).read()
        else:
            _Configuration(emit, data).read()
        version = data.get("format_version")
        return emit.findings, emit.diagnostics, version if isinstance(version, str) else None
