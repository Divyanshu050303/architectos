"""The source adapter contract, and reading a request's artifacts through it.

An adapter is pure and deterministic. For one parsed artifact it **detects** whether the artifact is
its format, and **extracts** findings — entities, their properties, explicit references, and
constructs it does not interpret — each with its location and how it is known. It never executes,
evaluates, templates or expands anything: a Terraform interpolation, a Compose ``${VARIABLE}`` and a
Helm ``{{ template }}`` are text, reported as such.

Reading a request (``read_artifacts``):

1. each artifact is parsed within the limits of ``loading`` (a failure is a diagnostic, and the
   artifact is ``failed``);
2. its format is the requested source type, or detected from its content; native Terraform (``.tf``,
   HCL) and anything unrecognized is ``unsupported``, with a diagnostic saying why;
3. its adapter extracts findings; an artifact with unsupported constructs or warnings is ``partial``.

**Secrets**: a value whose name looks like a secret (the Architecture IR's own pattern), every
environment variable's literal value, every Secret's data and every connection string's credentials
are never kept — the finding records that a value exists and is redacted. A connection string
contributes only its host, as a reference.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from core.architecture_ir.diff import SECRET_FIELD
from core.domain.discovery.findings import Diagnostic, Finding, SourceArtifact, SourceLocation
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.discovery.values import (
    KEY,
    ArtifactStatus,
    FindingType,
    Severity,
    SourceType,
    Verification,
)

from .loading import Document, Parsed, parse

MAX_FINDINGS_PER_ARTIFACT = 5000
MAX_KEPT_TEXT = 2000
MAX_KEPT_ITEMS = 100
HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*$")
HOST_PORT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9.-]{0,252}):([0-9]{1,5})$")
TEMPLATE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
SCALAR = str | int | float | bool


def artifact_key(path: str) -> str:
    """A stable entity key for findings about an artifact itself (its path may not be a valid key)."""
    return "artifact:" + hashlib.sha256(path.encode()).hexdigest()[:16]


def secret_name(name: str) -> bool:
    return bool(SECRET_FIELD.search(name))


def host_of(value: object) -> str | None:
    """The host a connection string or ``host:port`` names — never its credentials, path or query."""
    if not isinstance(value, str) or len(value) > MAX_KEPT_TEXT:
        return None
    text = value.strip()
    if "://" in text:
        try:
            host = urlsplit(text).hostname
        except ValueError:
            return None
        return host if host and HOST.fullmatch(host) else None
    match = HOST_PORT.fullmatch(text)
    return match.group(1) if match and HOST.fullmatch(match.group(1)) else None


def _kept(value: Any) -> Any:
    """A value small enough to keep — scalars, and short lists or objects of scalars — else None.
    Secret-looking keys of an object are dropped."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_KEPT_TEXT else None
    if isinstance(value, list) and len(value) <= MAX_KEPT_ITEMS and all(isinstance(v, SCALAR) for v in value):
        return value
    if (
        isinstance(value, dict)
        and len(value) <= MAX_KEPT_ITEMS
        and all(isinstance(v, SCALAR) for v in value.values())
    ):
        return {k: v for k, v in value.items() if not secret_name(k)}
    return None


class Emitter:
    """Collects one document's findings and diagnostics, each located in it."""

    def __init__(self, path: str, document: Document, extractor: str) -> None:
        self.path, self.document, self.extractor = path, document, extractor
        self.findings: list[Finding] = []
        self.diagnostics: list[Diagnostic] = []
        self.truncated = False

    def at(self, pointer: str | None) -> SourceLocation:
        return SourceLocation(
            self.path, self.document.index, pointer or None, self.document.lines.get(pointer or "")
        )

    def _add(self, finding: Finding) -> None:
        if len(self.findings) >= MAX_FINDINGS_PER_ARTIFACT:
            if not self.truncated:
                message = (
                    f"More than {MAX_FINDINGS_PER_ARTIFACT} findings in one document: the rest is not read."
                )
                self.diagnose("too_many_findings", Severity.ERROR, message, None)
                self.truncated = True
            return
        self.findings.append(finding)

    def entity(self, key: str, pointer: str) -> bool:
        if not KEY.fullmatch(key):
            self.diagnose(
                "invalid_name", Severity.WARNING, "A name cannot be used as an identifier.", pointer
            )
            return False
        self._add(Finding(FindingType.ENTITY, key, self.at(pointer), Verification.OBSERVED, self.extractor))
        return True

    def property(
        self,
        key: str,
        name: str,
        source_property: str,
        value: Any,
        pointer: str,
        *,
        secret: bool = False,
        verification: Verification = Verification.OBSERVED,
    ) -> None:
        hidden = secret or secret_name(source_property.rsplit(".", 1)[-1])
        self._add(
            Finding(
                FindingType.PROPERTY, key, self.at(pointer), verification, self.extractor,
                property=name, source_property=source_property, value=None if hidden else _kept(value),
                redacted=hidden and value is not None,
            )
        )  # fmt: skip

    def reference(self, key: str, target: str, pointer: str, source_property: str) -> None:
        finding = Finding(
            FindingType.REFERENCE, key, self.at(pointer), Verification.OBSERVED, self.extractor,
            source_property=source_property, target=target[:500],
        )  # fmt: skip
        self._add(finding)

    def unsupported(self, key: str, pointer: str | None, code: str, message: str) -> None:
        location = self.at(pointer)
        warning = (message,)
        self._add(
            Finding(
                FindingType.UNSUPPORTED,
                key,
                location,
                Verification.UNSUPPORTED,
                self.extractor,
                warnings=warning,
            )
        )
        self.diagnostics.append(Diagnostic(code, Severity.WARNING, message, location))

    def diagnose(self, code: str, severity: Severity, message: str, pointer: str | None) -> None:
        self.diagnostics.append(Diagnostic(code, severity, message, self.at(pointer)))


type Extracted = tuple[list[Finding], list[Diagnostic], str | None]


class SourceAdapter(Protocol):
    @property
    def source_type(self) -> SourceType: ...

    @property
    def version(self) -> int: ...

    def detect(self, path: str, documents: tuple[Document, ...]) -> bool: ...

    def extract(self, path: str, documents: tuple[Document, ...]) -> Extracted:
        """Findings, diagnostics and the format version the artifact declares (if any)."""
        ...


@dataclass(frozen=True)
class Extraction:
    artifacts: tuple[SourceArtifact, ...]
    findings: tuple[Finding, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    extractors: dict[str, int] = field(default_factory=dict)


def _note(path: str, code: str, message: str) -> Diagnostic:
    return Diagnostic(code, Severity.WARNING, message, SourceLocation(path))


def _record(artifact: ArtifactInput, status: ArtifactStatus, parsed: Parsed | None = None) -> SourceArtifact:
    documents = len(parsed.documents) if parsed else 0
    return SourceArtifact(
        artifact.path, artifact.content_hash, artifact.size_bytes, status, documents=documents
    )


def _read(
    artifact: ArtifactInput, requested: SourceType | None, adapters: dict[SourceType, SourceAdapter]
) -> tuple[SourceArtifact, list[Finding], list[Diagnostic], SourceAdapter | None]:
    path = artifact.path
    if path.endswith((".tf", ".tfvars")):
        message = (
            "Native Terraform (HCL) is not read; supply Terraform JSON (.tf.json or `terraform show -json`)."
        )
        return (
            _record(artifact, ArtifactStatus.UNSUPPORTED),
            [],
            [_note(path, "hcl_not_supported", message)],
            None,
        )
    parsed = parse(path, artifact.content)
    if parsed.failed:
        notes = list(parsed.diagnostics)
        if TEMPLATE.search(artifact.content):
            message = (
                "The artifact contains templates ({{ }}); they are never rendered. Supply rendered output."
            )
            notes.append(_note(path, "template_not_rendered", message))
        return _record(artifact, ArtifactStatus.FAILED), [], notes, None
    adapter = (
        adapters[requested]
        if requested is not None
        else next((a for a in adapters.values() if a.detect(path, parsed.documents)), None)
    )
    if adapter is None:
        note = _note(path, "unrecognized_format", "No supported source format recognizes this artifact.")
        return _record(artifact, ArtifactStatus.UNSUPPORTED, parsed), [], [*parsed.diagnostics, note], None
    findings, diagnostics, version = adapter.extract(path, parsed.documents)
    notes = [*parsed.diagnostics, *diagnostics]
    failed = not findings and any(d.severity is Severity.ERROR for d in notes)  # nothing could be read
    partial = any(d.severity is not Severity.INFO for d in notes) or any(
        f.type is FindingType.UNSUPPORTED for f in findings
    )
    status = ArtifactStatus.FAILED if failed else ArtifactStatus.PARTIAL if partial else ArtifactStatus.PARSED
    record = SourceArtifact(
        path, artifact.content_hash, artifact.size_bytes, status,
        adapter.source_type, version, len(parsed.documents),
    )  # fmt: skip
    return record, findings, notes, adapter


def read_artifacts(request: DiscoveryRequest, adapters: tuple[SourceAdapter, ...]) -> Extraction:
    """Every artifact of the request, parsed and extracted by its adapter — or why not."""
    by_type = {a.source_type: a for a in adapters}
    artifacts: list[SourceArtifact] = []
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []
    used: dict[str, int] = {}
    for artifact in request.artifacts:
        record, found, notes, adapter = _read(artifact, request.source_type, by_type)
        artifacts.append(record)
        findings += found
        diagnostics += notes
        if adapter is not None:
            used[adapter.source_type.value] = adapter.version
    return Extraction(tuple(artifacts), tuple(findings), tuple(diagnostics), used)
