"""Reading untrusted YAML and JSON: bounded, inert, and faithful to what is written.

Every artifact is untrusted input. Reading it never executes, evaluates or expands anything:

- **YAML** is read with a safe loader that also refuses aliases and anchors (no billion-laughs
  expansion) and resolves only YAML 1.2 core scalars: ``80:80`` stays the string it is (YAML 1.1
  would read the integer 4880), ``no`` stays a string, a date stays text, and a number with a
  leading zero (``012``, octal in YAML 1.1) stays the text it is. Tags beyond the standard ones are
  refused.
- **JSON** refuses ``NaN`` and ``Infinity``, and its nesting is measured before it is parsed.
- **Limits**: documents per artifact, nesting depth and values per artifact are bounded; exceeding
  one is a diagnostic and the artifact is not read further.
- **Duplicate keys** are reported (the last one is what the parsers keep), never silent.

Each YAML document comes with a map from a path within it (``spec.template.spec.containers[0].image``)
to its line, so findings can say where they were read.
"""

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from core.domain.discovery.findings import Diagnostic, SourceLocation
from core.domain.discovery.values import Severity

MAX_DOCUMENTS = 200
MAX_DEPTH = 32
MAX_NODES = 100_000

INT = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$|^0o[0-7]+$|^0x[0-9a-fA-F]+$")
FLOAT = re.compile(
    r"^[-+]?(?:[0-9]*\.[0-9]+|[0-9]+\.[0-9]*)(?:[eE][-+]?[0-9]+)?$|^[-+]?[0-9]+[eE][-+]?[0-9]+$"
)
BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
ALLOWED_TAGS = frozenset(
    {
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:null",
    }
)


class _Loader(yaml.SafeLoader):
    """The safe loader with YAML 1.2 core scalars only, and no aliases or anchors."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases are not allowed")
        if getattr(self.peek_event(), "anchor", None):  # type: ignore[no-untyped-call]
            raise yaml.YAMLError("anchors are not allowed")
        return super().compose_node(parent, index)


# Replace YAML 1.1 implicit resolution (sexagesimal numbers, yes/no booleans, timestamps).
_Loader.yaml_implicit_resolvers = {}
for _tag, _pattern, _first in (
    ("tag:yaml.org,2002:bool", BOOL, list("tTfF")),
    ("tag:yaml.org,2002:int", INT, list("-+0123456789")),
    ("tag:yaml.org,2002:float", FLOAT, list("-+.0123456789")),
    ("tag:yaml.org,2002:null", NULL, ["~", "n", "N", ""]),
):
    _Loader.add_implicit_resolver(_tag, _pattern, _first)


@dataclass(frozen=True)
class Document:
    index: int
    data: Any
    lines: Mapping[str, int] = field(default_factory=dict)  # path within the document -> line (1-based)


@dataclass(frozen=True)
class Parsed:
    documents: tuple[Document, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    failed: bool = False


def _failure(path: str, code: str, message: str, line: int | None = None) -> Parsed:
    diagnostic = Diagnostic(code, Severity.ERROR, message, SourceLocation(path, None, None, line))
    return Parsed((), (diagnostic,), True)


def child(pointer: str, key: object) -> str:
    """The path of ``key`` under ``pointer``: ``a.b``, ``a[0]``."""
    if isinstance(key, int):
        return f"{pointer}[{key}]"
    return f"{pointer}.{key}" if pointer else str(key)


class _Limit(Exception):
    def __init__(self, code: str, message: str, node: yaml.Node) -> None:
        super().__init__(message)
        self.code, self.message, self.line = code, message, node.start_mark.line + 1


class _Walk:
    """Checks a composed YAML document (depth, size, tags, duplicate keys) and maps paths to lines."""

    def __init__(self, path: str, document: int, nodes: int) -> None:
        self.path, self.document, self.nodes = path, document, nodes
        self.lines: dict[str, int] = {}
        self.warnings: list[Diagnostic] = []

    def visit(self, node: yaml.Node, pointer: str, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_DEPTH:
            raise _Limit("too_deep", f"Nesting deeper than {MAX_DEPTH} levels.", node)
        if self.nodes > MAX_NODES:
            raise _Limit("too_large", f"More than {MAX_NODES} values in one artifact.", node)
        if node.tag not in ALLOWED_TAGS:
            raise _Limit("unsupported_tag", "Only standard YAML tags are read.", node)
        self.lines.setdefault(pointer, node.start_mark.line + 1)
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key, value in node.value:
                name = str(key.value)
                if name in seen:
                    where = SourceLocation(
                        self.path, self.document, child(pointer, name), key.start_mark.line + 1
                    )
                    message = f"The key {name!r} appears twice; the last is read."
                    self.warnings.append(Diagnostic("duplicate_key", Severity.WARNING, message, where))
                seen.add(name)
                self.visit(value, child(pointer, name), depth + 1)
        elif isinstance(node, yaml.SequenceNode):
            for index, value in enumerate(node.value):
                self.visit(value, child(pointer, index), depth + 1)


def _plain(value: Any) -> Any:
    """JSON-compatible data: mapping keys as strings (YAML allows others)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def parse_yaml(path: str, content: str) -> Parsed:
    loader = _Loader(content)
    documents: list[Document] = []
    warnings: list[Diagnostic] = []
    nodes = 0
    try:
        while loader.check_node():
            if len(documents) >= MAX_DOCUMENTS:
                return _failure(path, "too_many_documents", f"More than {MAX_DOCUMENTS} documents.")
            node = loader.get_node()
            if node is None:
                break
            visit = _Walk(path, len(documents), nodes)
            visit.visit(node, "", 0)
            nodes = visit.nodes
            warnings += visit.warnings
            documents.append(Document(len(documents), _plain(loader.construct_document(node)), visit.lines))
    except _Limit as limit:
        return _failure(path, limit.code, limit.message, limit.line)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        return _failure(
            path, "malformed_yaml", "The YAML could not be read.", mark.line + 1 if mark else None
        )
    finally:
        loader.dispose()
    return Parsed(tuple(d for d in documents if d.data is not None), tuple(warnings))


def _json_depth(content: str) -> int:
    """The deepest nesting of a JSON text, measured before parsing it."""
    depth = deepest = 0
    in_string = escaped = False
    for char in content:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif char in "]}":
            depth -= 1
    return deepest


class _TooLarge(Exception):
    pass


def parse_json(path: str, content: str) -> Parsed:
    if _json_depth(content) > MAX_DEPTH:
        return _failure(path, "too_deep", f"Nesting deeper than {MAX_DEPTH} levels.")
    duplicates: list[str] = []
    nodes = 0

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal nodes
        nodes += len(items) + 1
        if nodes > MAX_NODES:
            raise _TooLarge
        found: dict[str, Any] = {}
        for name, value in items:
            if name in found:
                duplicates.append(name)
            found[name] = value
        return found

    def constant(name: str) -> Any:
        raise ValueError(f"{name} is not a JSON number")

    try:
        data = json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
    except _TooLarge:
        return _failure(path, "too_large", f"More than {MAX_NODES} values in one artifact.")
    except ValueError:  # includes json.JSONDecodeError
        return _failure(path, "malformed_json", "The JSON could not be read.")
    warnings = tuple(
        Diagnostic(
            "duplicate_key", Severity.WARNING, f"The key {name!r} appears twice; the last is read.",
            SourceLocation(path, 0),
        )
        for name in sorted(set(duplicates))
    )  # fmt: skip
    return Parsed((Document(0, data),), warnings)


def parse(path: str, content: str) -> Parsed:
    """JSON when the text is a JSON object or array, YAML otherwise (one or more documents)."""
    if content.lstrip().startswith(("{", "[")):
        return parse_json(path, content)
    return parse_yaml(path, content)


def walk(value: Any, pointer: str = "") -> Iterator[tuple[str, Any]]:
    """Every (path, value) pair of a document, depth-first, in document order."""
    yield pointer, value
    if isinstance(value, dict):
        for name, item in value.items():
            yield from walk(item, child(pointer, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, child(pointer, index))
