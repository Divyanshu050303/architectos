"""Docker Compose files, as declared — what Compose would be asked to run, never what runs.

Read: each service's image, a declared build (never built), ports as written, ``deploy.replicas``,
resource limits, a declared health check, networks, the names of environment variables (values
never kept; a connection string contributes its host as a reference), and its explicit references:
``depends_on``, ``links`` and named volumes. Top-level named volumes and networks are read as
entities. ``${VARIABLES}`` are never expanded (reported); ``extends`` and ``include`` need other
files and are reported as unsupported.
"""

from typing import Any

from core.domain.discovery.values import Severity, SourceType

from .adapters import Emitter, Extracted, artifact_key, host_of
from .loading import Document, walk

EXTRACTOR = "docker_compose@1"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def service_key(project: str | None, kind: str, name: str) -> str:
    return f"compose:{project}/{kind}/{name}" if project else f"compose:{kind}/{name}"


def _environment(value: Any) -> list[tuple[str, Any]]:
    """(name, value) pairs of a list (``NAME=value``) or mapping environment."""
    if isinstance(value, dict):
        return [(str(k), v) for k, v in value.items()]
    pairs: list[tuple[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            name, equals, text = item.partition("=")
            pairs.append((name, text if equals else None))
    return pairs


def _named_volume(volume: Any) -> str | None:
    if isinstance(volume, str):
        source = volume.split(":", 1)[0] if ":" in volume else None
    else:
        long_form = _mapping(volume)
        source = long_form.get("source") if long_form.get("type", "volume") == "volume" else None
    if isinstance(source, str) and source and not source.startswith((".", "/", "~", "$")):
        return source
    return None


class _Service:
    def __init__(self, emit: Emitter, project: str | None, name: str, data: dict[str, Any]) -> None:
        self.emit, self.data, self.name = emit, data, name
        self.key = service_key(project, "service", name)
        self.base = f"services.{name}"

    def prop(self, name: str, path: str, value: Any) -> None:
        if value is not None:
            self.emit.property(self.key, name, path, value, f"{self.base}.{path}")

    def ref(self, target: str, path: str) -> None:
        self.emit.reference(self.key, target, f"{self.base}.{path}", path)

    def read(self) -> None:
        if not self.emit.entity(self.key, self.base):
            return
        self.runtime(self.data)
        self.environment(self.data)
        self.references(self.data)
        if "extends" in self.data:
            message = f"Service {self.name} extends another definition, which is not read."
            self.emit.unsupported(self.key, f"{self.base}.extends", "extends_not_read", message)

    def runtime(self, data: dict[str, Any]) -> None:
        self.prop("image", "image", data.get("image"))
        if "build" in data:  # declared, never built
            build = data["build"]
            self.prop(
                "build",
                "build",
                build if isinstance(build, str) else _mapping(build).get("context", "declared"),
            )
        if isinstance(data.get("ports"), list):
            ports = [p if isinstance(p, str | int) else _mapping(p).get("target") for p in data["ports"]]
            self.prop("ports", "ports", [p for p in ports if p is not None])
        deploy = _mapping(data.get("deploy"))
        if isinstance(deploy.get("replicas"), int):
            self.prop("replicas", "deploy.replicas", deploy["replicas"])
        limits = _mapping(_mapping(deploy.get("resources")).get("limits"))
        if limits.get("cpus") is not None:
            self.prop("cpu_limit", "deploy.resources.limits.cpus", str(limits["cpus"]))
        self.prop("memory_limit", "deploy.resources.limits.memory", limits.get("memory"))
        if "healthcheck" in data:
            self.prop("health_check", "healthcheck", not bool(_mapping(data["healthcheck"]).get("disable")))
        networks = data.get("networks")
        if isinstance(networks, list | dict):
            self.prop("networks", "networks", sorted(str(n) for n in networks))

    def environment(self, data: dict[str, Any]) -> None:
        for index, (name, value) in enumerate(_environment(data.get("environment"))):
            self.prop("environment_variable", f"environment[{index}]", name)  # the value is never kept
            host = host_of(value)
            if host:
                self.ref(f"host/{host}", f"environment[{index}]")

    def references(self, data: dict[str, Any]) -> None:
        dependencies = data.get("depends_on")
        for name in dependencies if isinstance(dependencies, list | dict) else []:
            self.ref(f"service/{name}", f"depends_on.{name}")
        for index, link in enumerate(data.get("links") or []):
            if isinstance(link, str):
                self.ref(f"service/{link.split(':', 1)[0]}", f"links[{index}]")
        for index, volume in enumerate(data.get("volumes") or []):
            source = _named_volume(volume)
            if source:
                self.ref(f"volume/{source}", f"volumes[{index}]")


class ComposeAdapter:
    source_type = SourceType.DOCKER_COMPOSE
    version = 1

    def detect(self, path: str, documents: tuple[Document, ...]) -> bool:
        if len(documents) != 1:
            return False
        data = documents[0].data
        return isinstance(data, dict) and isinstance(data.get("services"), dict) and "apiVersion" not in data

    def extract(self, path: str, documents: tuple[Document, ...]) -> Extracted:
        document = documents[0]
        emit = Emitter(path, document, EXTRACTOR)
        data = _mapping(document.data)
        project = data.get("name") if isinstance(data.get("name"), str) else None
        for name, service in _mapping(data.get("services")).items():
            if isinstance(service, dict):
                _Service(emit, project, str(name), service).read()
        for kind in ("volumes", "networks"):
            for name in _mapping(data.get(kind)):
                emit.entity(service_key(project, kind[:-1], str(name)), f"{kind}.{name}")
        if "include" in data:
            emit.unsupported(
                artifact_key(path), "include", "include_not_read", "Included Compose files are not read."
            )
        if any(isinstance(v, str) and "${" in v for _, v in walk(data)):
            message = "Values contain ${VARIABLES}; they are kept as written, never expanded."
            emit.diagnose("variable_not_expanded", Severity.INFO, message, None)
        version = data.get("version")
        return (
            emit.findings,
            emit.diagnostics,
            str(version) if isinstance(version, str | int | float) else None,
        )
