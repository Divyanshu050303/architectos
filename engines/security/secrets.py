"""Secrets analysis: how the architecture models the secrets its components use, and settings in
the architecture itself that look like secrets. Only the architecture is examined — never source
code, repositories or user files — and no secret value is ever read beyond whether one is present,
logged, stored or shown: settings are reported by name, their values redacted.

- ``secret_source: hardcoded`` is a declared control gap (``hardcoded_secret``): the secret is part
  of the component's code or image.
- A component declaring ``secrets_required: true`` without a declared ``secret_source`` cannot be
  evaluated (``secret_source_not_modeled``).
- A setting preserved in the architecture (``configuration.extra`` or ``metadata``, at any depth)
  whose name looks like a secret (``password``, ``token``, ``api_key``, ``private_key``, …) and has a
  value is a potential risk (``secret_in_configuration``): if the value is the secret, everyone who
  can read the architecture can read it; if it is a reference, ``secret_source`` says so better.
  Typed properties with a closed set of values (``authorization``, ``secret_source``) cannot hold a
  secret and are not reported; a boolean or empty value is not a secret.

Whether secrets are rotated, or who may read them, is checked only when a policy requires it.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.architecture_ir.values import Json
from core.domain.capacity.results import Certainty
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.security.values import is_secret_path, redacted
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import certainty, evidence, finding

T = FindingType
SOURCE_FACTS = ("secrets_required", "secret_source")
MAX_DEPTH = 10  # preserved settings are bounded by the IR; nesting deeper than this is not searched


def secret_settings(prefix: str, value: Json, depth: int = 0) -> Iterator[str]:
    """The paths under ``prefix`` whose name looks like a secret and whose value is present (not
    None, empty or a boolean), in a fixed order. The values are only tested, never returned."""
    if isinstance(value, Mapping):
        for key in sorted(value):
            path, item = f"{prefix}.{key}", value[key]
            if is_secret_path(path) and not (item is None or item == "" or isinstance(item, bool)):
                yield path
            elif depth < MAX_DEPTH:
                yield from secret_settings(path, item, depth + 1)
    elif isinstance(value, list | tuple) and depth < MAX_DEPTH:
        for index, item in enumerate(value):
            yield from secret_settings(f"{prefix}[{index}]", item, depth + 1)


class Secrets:
    meta = AnalyzerMeta(
        id="secrets",
        version=1,
        name="Secrets",
        description="How components get their secrets, and settings in the architecture that look like "
        "secrets (reported by name only).",
        category=FindingCategory.SECRETS,
        finding_types=(T.HARDCODED_SECRET, T.SECRET_SOURCE_NOT_MODELED, T.SECRET_IN_CONFIGURATION),
        inputs=("components", "connections"),
        properties=SOURCE_FACTS,
        rules=(
            "A component declaring secret_source hardcoded has its secret in its code or image.",
            "A component declaring secrets_required without a secret_source cannot be evaluated.",
            "A preserved setting or metadata entry named like a secret, with a value, is a potential "
            "exposure of that secret through the architecture itself.",
        ),
        unsupported=(
            "Source code, repositories, images and files (never scanned).",
            "Whether a value named like a secret is a literal secret or a reference.",
            "Rotation and access to secrets, unless a policy requires them.",
        ),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        findings = [f for node in context.components if (f := self._source(context, node))]
        elements = [(n.id, n.configuration.extra, n.metadata, "node_ids") for n in context.components]
        elements += [(c.id, c.configuration.extra, c.metadata, "connection_ids") for c in context.connections]
        for element_id, extra, metadata, field in elements:
            paths = [
                *secret_settings(f"{element_id}.configuration.extra", dict(extra)),
                *secret_settings(f"{element_id}.metadata", dict(metadata)),
            ]
            if paths:
                findings.append(self._settings(element_id, paths, field))
        return AnalyzerOutput(tuple(findings))

    def _source(self, context: SecurityContext, node: Node) -> SecurityFinding | None:
        if node.kind is NodeKind.EXTERNAL:
            return None
        facts = context.facts[node.id]
        source = facts.known("secret_source")
        common: dict[str, Any] = {"node_ids": (node.id,), "evidence": evidence(facts, SOURCE_FACTS)}
        if source == "hardcoded":
            return finding(
                self.meta,
                T.HARDCODED_SECRET,
                Severity.HIGH,
                certainty((facts, SOURCE_FACTS)),
                title=f"{node.id} has its secrets hardcoded",
                explanation=f"{node.id} declares secret_source hardcoded: its secrets are part of its code "
                "or image, readable by anyone with access to them, and changing one needs a new build.",
                recommendation=f"Review moving the secrets of {node.id} to a secret manager.",
                **common,
            )
        if facts.known("secrets_required") is True and source is None:
            return finding(
                self.meta,
                T.SECRET_SOURCE_NOT_MODELED,
                Severity.MEDIUM,
                certainty((facts, SOURCE_FACTS)),
                title=f"Where {node.id} gets its secrets is not modeled",
                explanation=f"{node.id} declares that it needs secrets, but not where they come from: how "
                "they are stored and who can read them cannot be judged.",
                recommendation=f"State the secret_source of {node.id}.",
                missing=(f"{node.id}.configuration.secret_source",),
                **common,
            )
        return None

    def _settings(self, element_id: str, paths: list[str], field: str) -> SecurityFinding:
        return finding(
            self.meta,
            T.SECRET_IN_CONFIGURATION,
            Severity.MEDIUM,
            Certainty.MODELED,
            title=f"{element_id} has settings named like secrets, with values, in the architecture",
            explanation=f"{names(paths)} {'is' if len(paths) == 1 else 'are'} named like a secret and "
            "has a value in the architecture. If the value is the secret itself, everyone who can read the "
            "architecture can read it; if it is a reference, secret_source models it better. The value is "
            "not shown here.",
            recommendation="Review whether these values are secrets; keep secrets out of the architecture "
            "and state where they come from (secret_source).",
            evidence=tuple(redacted(path, "") for path in paths),
            **{field: (element_id,)},
        )
