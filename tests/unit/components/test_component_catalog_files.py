"""The catalog's files (ARCH-COMP-001, phase 2): the repository's catalog loads and is locked; every
technology of the specification is listed with an explicit support state; the published JSON Schema
is generated from the code; files are untrusted input (safe YAML without aliases, bounded, in their
place); a published version is never rewritten."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from core.domain.components.entities import CATEGORIES, SupportStatus
from core.domain.components.errors import InvalidCatalog
from core.domain.components.schema import json_schema, render
from persistence.component_catalog import (
    DEFAULT_ROOT,
    LOCK_FILE,
    MAX_FILE_BYTES,
    default_catalog,
    load_catalog,
    lock,
)
from tests.unit.components.test_component_specifications import spec

SCHEMA_FILE = Path(__file__).resolve().parents[3] / "core" / "schemas" / "component.schema.json"
LISTED = {  # ARCH-COMP-001 section 3: the initial catalog
    "compute": {
        "virtual-machine", "container", "kubernetes", "serverless", "aws-lambda", "google-cloud-run",
        "aws-ecs",
    },
    "databases": {
        "postgresql", "mysql", "mongodb", "aws-dynamodb", "cassandra", "scylladb", "redis", "clickhouse",
        "elasticsearch",
    },
    "messaging": {"kafka", "rabbitmq", "nats", "aws-sqs", "aws-sns", "google-pubsub", "pulsar"},
    "storage": {"aws-s3", "google-cloud-storage", "azure-blob-storage", "aws-efs"},
    "networking": {"load-balancer", "api-gateway", "cdn", "dns", "waf", "service-mesh"},
    "observability": {"prometheus", "grafana", "opentelemetry", "jaeger", "loki", "elk-stack"},
}  # fmt: skip


def validator() -> Draft202012Validator:
    Draft202012Validator.check_schema(json_schema())
    return Draft202012Validator(json_schema(), format_checker=FormatChecker())


class _PlainDumper(yaml.SafeDumper):
    """Writes shared values out in full: the catalog refuses YAML aliases."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def write(root: Path, relative: str, data: Any) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.dump(data, Dumper=_PlainDumper, sort_keys=False))
    return path


def reason(root: Path) -> str:
    with pytest.raises(InvalidCatalog) as error:
        load_catalog(root)
    return str(error.value.details["reason"])


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write(tmp_path, "databases/example-sql.yaml", spec())
    lock(tmp_path)
    return tmp_path


def test_the_repository_catalog_loads_and_lists_every_specified_technology() -> None:
    catalog = default_catalog()
    listed = {f"{directory}/{entry}" for directory, entries in LISTED.items() for entry in entries}
    assert {s.id for s in catalog.list()} == listed
    assert len(catalog) == 39
    for summary in catalog.categories():
        assert summary.components == len(LISTED[summary.category.directory])
    # nothing is claimed complete that is not specified and tested
    for entry in catalog.list():
        assert entry.support_status is SupportStatus.PLANNED or entry.capabilities, entry.id
    assert {c.id for c in CATEGORIES.values()} == {s.category.id for s in catalog.categories()}


def test_every_repository_version_is_locked() -> None:
    recorded = json.loads((DEFAULT_ROOT / LOCK_FILE).read_text())["versions"]
    catalog = default_catalog()
    assert recorded == {v.ref: v.content_hash for s in catalog.list() for v in catalog.history(s.id)}


def test_the_published_schema_is_up_to_date_and_describes_every_entry() -> None:
    assert SCHEMA_FILE.read_text() == render(), "run `make schemas`"
    check = validator()
    for entry in default_catalog().list():
        assert [e.message for e in check.iter_errors(entry.to_dict())] == [], entry.id
    assert [e.message for e in check.iter_errors(spec())] == []


def test_the_schema_refuses_what_the_code_refuses() -> None:
    data = spec(price="0.10", category="quantum")
    data["capabilities"][0]["state"] = "probably"
    assert len(list(validator().iter_errors(data))) == 3


def test_older_versions_are_read_from_history(root: Path) -> None:
    write(root, "databases/history/example-sql@1.yaml", (root / "databases/example-sql.yaml").read_text())
    write(root, "databases/example-sql.yaml", spec(version=2, description="Version 2."))
    assert lock(root) == ["databases/example-sql@2"]
    catalog = load_catalog(root)
    assert [s.version for s in catalog.history("databases/example-sql")] == [1, 2]


def test_a_published_version_edited_in_place_is_refused(root: Path) -> None:
    write(root, "databases/example-sql.yaml", spec(description="Quietly changed."))
    assert reason(root) == "changed_without_new_version"
    with pytest.raises(InvalidCatalog):
        lock(root)  # the lock refuses to record the change


@pytest.mark.parametrize(
    ("relative", "content", "expected"),
    [
        ("databases/other.yaml", spec(), "misplaced_specification"),  # its id is not its place
        ("databases/history/example-sql@1.yaml", spec(), "misplaced_specification"),  # not older than current
        ("quantum/thing.yaml", spec(), "unexpected_file"),  # not a category directory
        ("databases/notes.txt", "notes", "unexpected_file"),
        (
            "databases/float.yaml",
            spec(
                id="databases/float",
                configuration=[
                    {
                        "property": "cpu_limit_cores",
                        "default": 0.5,
                        "provenance": {"kind": "documented", "sources": ["docs"]},
                    }
                ],
            ),
            "invalid_specification",
        ),
        ("databases/alias.yaml", "a: &x [1]\nb: *x\n", "unreadable_file"),  # no aliases
        ("databases/tagged.yaml", "!!python/object/apply:os.system ['true']\n", "unreadable_file"),
        ("databases/list.yaml", "- 1\n- 2\n", "invalid_specification"),
    ],
)
def test_files_are_untrusted_input(root: Path, relative: str, content: Any, expected: str) -> None:
    write(root, relative, content)
    assert reason(root) == expected


def test_files_are_bounded_and_links_are_not_followed(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    big = write(root, "databases/big.yaml", "a: " + "x" * MAX_FILE_BYTES)
    assert reason(root) == "unreadable_file"
    big.unlink()
    outside = write(tmp_path_factory.mktemp("outside"), "outside.yaml", spec(id="databases/linked"))
    (root / "databases" / "linked.yaml").symlink_to(outside)
    assert reason(root) in {"unexpected_file", "unreadable_file"}


def test_invalid_specifications_name_their_file_and_fields(root: Path) -> None:
    write(root, "databases/broken.yaml", spec(id="databases/broken", technology="Broken SQL"))
    with pytest.raises(InvalidCatalog) as error:
        load_catalog(root)
    assert error.value.details == {
        "reason": "invalid_specification",
        "file": "databases/broken.yaml",
        "fields": ["technology"],
    }
