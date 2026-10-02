"""Kubernetes manifests (YAML or JSON, one or many documents, ``kind: List`` included).

Read, as declared in the manifest — the **desired state**, never the running cluster:

- workloads (Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob, Pod): replicas, container
  images, ports, resource requests and limits, pod labels, the service account, the names of
  environment variables (values never kept; a connection string contributes its host as a
  reference), and explicit references to ConfigMaps, Secrets and PersistentVolumeClaims;
- Services: type, ports, the selector (a reference to the workloads it selects), an ExternalName;
- Ingresses: class, hosts, and the Services they route to;
- ConfigMaps and Secrets: their key names only — never their values;
- PersistentVolumeClaims: requested storage, storage class, access modes.

Any other kind is reported as unsupported. Values holding templates (``{{ }}``) are reported:
templates are never rendered.
"""

from typing import Any

from core.domain.discovery.findings import Diagnostic, Finding
from core.domain.discovery.values import Severity, SourceType

from .adapters import TEMPLATE, Emitter, Extracted, artifact_key, host_of
from .loading import Document, child, walk

WORKLOADS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"})
READ = WORKLOADS | {"Service", "Ingress", "ConfigMap", "Secret", "PersistentVolumeClaim"}
SCALED = frozenset({"Deployment", "StatefulSet", "ReplicaSet"})
EXTRACTOR = "kubernetes@1"


def entity_key(kind: str, name: str, namespace: str | None) -> str:
    where = f"{namespace}/" if namespace else ""
    return f"kubernetes:{where}{kind.lower()}/{name}"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pod_spec(kind: str, spec: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if kind == "Pod":
        return spec, "spec"
    if kind == "CronJob":
        template = _mapping(_mapping(_mapping(spec.get("jobTemplate")).get("spec")).get("template"))
        pod = template.get("spec")
        return (pod if isinstance(pod, dict) else None), "spec.jobTemplate.spec.template.spec"
    pod = _mapping(spec.get("template")).get("spec")
    return (pod if isinstance(pod, dict) else None), "spec.template.spec"


class _Object:
    def __init__(self, emit: Emitter, data: dict[str, Any], pointer: str) -> None:
        self.emit, self.data, self.pointer = emit, data, pointer
        self.metadata = _mapping(data.get("metadata"))
        self.kind = str(data.get("kind"))
        name, namespace = self.metadata.get("name"), self.metadata.get("namespace")
        self.name = name if isinstance(name, str) else None
        self.namespace = namespace if isinstance(namespace, str) else None
        self.key = entity_key(self.kind, self.name, self.namespace) if self.name else ""

    def at(self, path: str) -> str:
        return child(self.pointer, path) if self.pointer else path

    def prop(self, name: str, path: str, value: Any) -> None:
        if value is not None:
            self.emit.property(self.key, name, path, value, self.at(path))

    def ref(self, target: str, path: str) -> None:
        self.emit.reference(self.key, target, self.at(path), path)

    def read(self) -> None:
        artifact = artifact_key(self.emit.path)
        if self.name is None:
            message = f"A {self.kind} without a name is not read."
            self.emit.unsupported(artifact, self.at("metadata"), "unnamed_object", message)
            return
        if self.kind not in READ:
            self.emit.unsupported(
                artifact, self.at("kind"), "unsupported_kind", f"{self.kind} {self.name} is not read."
            )
            return
        if not self.emit.entity(self.key, self.at("metadata.name")):
            return
        self.prop("kind", "kind", self.kind)
        self.prop("namespace", "metadata.namespace", self.namespace)
        if isinstance(self.metadata.get("labels"), dict):
            self.prop("labels", "metadata.labels", self.metadata["labels"])
        spec = _mapping(self.data.get("spec"))
        readers = {
            "Service": self.service,
            "Ingress": self.ingress,
            "ConfigMap": self.keys,
            "Secret": self.keys,
            "PersistentVolumeClaim": self.claim,
        }
        readers.get(self.kind, self.workload)(spec)

    # --- kinds -----------------------------------------------------------------------------------

    def workload(self, spec: dict[str, Any]) -> None:
        if self.kind in SCALED and isinstance(spec.get("replicas"), int):
            self.prop("replicas", "spec.replicas", spec["replicas"])
        pod, path = _pod_spec(self.kind, spec)
        if pod is None:
            return
        if self.kind != "Pod":
            labels = _mapping(_mapping(spec.get("template")).get("metadata")).get("labels")
            if isinstance(labels, dict):
                self.prop("pod_labels", "spec.template.metadata.labels", labels)
        if isinstance(pod.get("serviceAccountName"), str):
            self.prop("service_account", f"{path}.serviceAccountName", pod["serviceAccountName"])
        for group in ("initContainers", "containers"):
            for index, container in enumerate(pod.get(group) or []):
                if isinstance(container, dict):
                    self.container(container, f"{path}.{group}[{index}]", init=group == "initContainers")
        for index, volume in enumerate(pod.get("volumes") or []):
            if isinstance(volume, dict):
                self.volume(volume, f"{path}.volumes[{index}]")
        for index, claim in enumerate(spec.get("volumeClaimTemplates") or []):
            name = _mapping(_mapping(claim).get("metadata")).get("name")
            if isinstance(name, str):
                self.prop("volume_claim", f"spec.volumeClaimTemplates[{index}].metadata.name", name)

    def container(self, container: dict[str, Any], path: str, *, init: bool) -> None:
        self.prop("init_image" if init else "image", f"{path}.image", container.get("image"))
        ports = [p.get("containerPort") for p in container.get("ports") or [] if isinstance(p, dict)]
        if ports:
            self.prop("ports", f"{path}.ports", [p for p in ports if isinstance(p, int)])
        resources = _mapping(container.get("resources"))
        for level, suffix in (("requests", "request"), ("limits", "limit")):
            amounts = _mapping(resources.get(level))
            for resource in ("cpu", "memory"):
                if resource in amounts:
                    self.prop(
                        f"{resource}_{suffix}", f"{path}.resources.{level}.{resource}", str(amounts[resource])
                    )
        for index, variable in enumerate(container.get("env") or []):
            if isinstance(variable, dict) and isinstance(variable.get("name"), str):
                self.env(variable, f"{path}.env[{index}]")
        for index, source in enumerate(container.get("envFrom") or []):
            for kind, field in (("configmap", "configMapRef"), ("secret", "secretRef")):
                name = _mapping(_mapping(source).get(field)).get("name")
                if isinstance(name, str):
                    self.ref(f"{kind}/{name}", f"{path}.envFrom[{index}].{field}.name")

    def env(self, variable: dict[str, Any], path: str) -> None:
        """The variable's name; its literal value is never kept (it may be a credential), but the
        host of a connection string is a reference."""
        self.prop("environment_variable", f"{path}.name", variable["name"])
        host = host_of(variable.get("value"))
        if host:
            self.ref(f"host/{host}", f"{path}.value")
        source = _mapping(variable.get("valueFrom"))
        for kind, field in (("secret", "secretKeyRef"), ("configmap", "configMapKeyRef")):
            name = _mapping(source.get(field)).get("name")
            if isinstance(name, str):
                self.ref(f"{kind}/{name}", f"{path}.valueFrom.{field}.name")

    def volume(self, volume: dict[str, Any], path: str) -> None:
        for kind, field, attribute in (
            ("configmap", "configMap", "name"),
            ("secret", "secret", "secretName"),
            ("persistentvolumeclaim", "persistentVolumeClaim", "claimName"),
        ):
            name = _mapping(volume.get(field)).get(attribute)
            if isinstance(name, str):
                self.ref(f"{kind}/{name}", f"{path}.{field}.{attribute}")

    def service(self, spec: dict[str, Any]) -> None:
        self.prop("service_type", "spec.type", spec.get("type"))
        ports = [p.get("port") for p in spec.get("ports") or [] if isinstance(p, dict)]
        if ports:
            self.prop("ports", "spec.ports", [p for p in ports if isinstance(p, int)])
        selector = spec.get("selector")
        if isinstance(selector, dict) and selector and all(isinstance(v, str) for v in selector.values()):
            self.ref("selector:" + ",".join(f"{k}={v}" for k, v in sorted(selector.items())), "spec.selector")
        if isinstance(spec.get("externalName"), str):
            self.ref(f"host/{spec['externalName']}", "spec.externalName")

    def ingress(self, spec: dict[str, Any]) -> None:
        self.prop("ingress_class", "spec.ingressClassName", spec.get("ingressClassName"))
        rules = [r for r in spec.get("rules") or [] if isinstance(r, dict)]
        hosts = [r["host"] for r in rules if isinstance(r.get("host"), str)]
        if hosts:
            self.prop("hosts", "spec.rules", hosts)
        default = _mapping(_mapping(spec.get("defaultBackend")).get("service")).get("name")
        if isinstance(default, str):
            self.ref(f"service/{default}", "spec.defaultBackend.service.name")
        for rule_index, rule in enumerate(spec.get("rules") or []):
            for path_index, entry in enumerate(_mapping(_mapping(rule).get("http")).get("paths") or []):
                name = _mapping(_mapping(_mapping(entry).get("backend")).get("service")).get("name")
                if isinstance(name, str):
                    where = f"spec.rules[{rule_index}].http.paths[{path_index}].backend.service.name"
                    self.ref(f"service/{name}", where)

    def keys(self, _spec: dict[str, Any]) -> None:
        names = sorted(
            {str(k) for field in ("data", "stringData", "binaryData") for k in _mapping(self.data.get(field))}
        )
        if names:  # the names of the keys, never their values
            self.prop("keys", "data", names)

    def claim(self, spec: dict[str, Any]) -> None:
        storage = _mapping(_mapping(spec.get("resources")).get("requests")).get("storage")
        if storage is not None:
            self.prop("storage_request", "spec.resources.requests.storage", str(storage))
        self.prop("storage_class", "spec.storageClassName", spec.get("storageClassName"))
        if isinstance(spec.get("accessModes"), list):
            self.prop("access_modes", "spec.accessModes", spec["accessModes"])


def _manifest(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("apiVersion"), str)
        and isinstance(value.get("kind"), str)
    )


class KubernetesAdapter:
    source_type = SourceType.KUBERNETES
    version = 1

    def detect(self, path: str, documents: tuple[Document, ...]) -> bool:
        return bool(documents) and all(_manifest(d.data) for d in documents)

    def extract(self, path: str, documents: tuple[Document, ...]) -> Extracted:
        findings: list[Finding] = []
        diagnostics: list[Diagnostic] = []
        for document in documents:
            emit = Emitter(path, document, EXTRACTOR)
            data = document.data
            if not _manifest(data):
                message = "A document that is not a Kubernetes object is not read."
                emit.unsupported(artifact_key(path), None, "not_a_manifest", message)
            elif data["kind"] == "List":
                for index, item in enumerate(data.get("items") or []):
                    if _manifest(item):
                        _Object(emit, item, f"items[{index}]").read()
            else:
                _Object(emit, data, "").read()
            if any(isinstance(v, str) and TEMPLATE.search(v) for _, v in walk(data)):
                message = "Values contain templates ({{ }}); they are read as text, never rendered."
                emit.diagnose("template_not_rendered", Severity.WARNING, message, None)
            findings += emit.findings
            diagnostics += emit.diagnostics
        return findings, diagnostics, None
