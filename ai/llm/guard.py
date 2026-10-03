"""What every model call of ArchitectOS shares: data kept apart from instructions, a model's output
checked for what it may never write, and only a hash of that output kept.

- ``delimited``: named sections of untrusted data, each inside its own data tag; nothing inside a
  section can open or close one (a tag in the data is escaped, in any case).
- ``unsafe``: output text with a URL, an IP address, or anything the knowledge redaction rules would
  redact (an assignment to a secret-looking name, a password in a URL, a bearer token, a private key)
  is refused — named by where, never by what.
- ``raw_output``: what is kept of a model's output — the SHA-256 and size of its canonical JSON.
"""

import hashlib
import json
import re
from collections.abc import Iterable

from core.domain.architecture_agent.results import Rejection
from core.domain.architecture_agent.runs import RawOutput
from engines.knowledge.redaction import redact

DATA_TAG = "agent_data"
MAX_REJECTIONS = 100
_TAG = re.compile(rf"<(/?){DATA_TAG}", re.IGNORECASE)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")  # a sentence's full stop ends it


def delimited(sections: Iterable[tuple[str, str]]) -> str:
    """Each (name, body) as delimited data; nothing inside a section can open or close one."""
    return "\n\n".join(
        f'<{DATA_TAG} section="{name}">\n{_TAG.sub(r"&lt;\1" + DATA_TAG, body)}\n</{DATA_TAG}>'
        for name, body in sections
    )


def _strings(value: object, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _strings(item, f"{path}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def unsafe(data: object) -> list[Rejection]:
    """Text the model may not write: URLs, IP addresses, credentials. Named by where, never what."""
    found: list[Rejection] = []
    for path, text in _strings(data, "$"):
        if _URL.search(text):
            found.append(Rejection("url_in_output", path[:200], "Output text may not contain a URL."))
        if _IPV4.search(text):
            found.append(
                Rejection("address_in_output", path[:200], "Output text may not contain an IP address.")
            )
        if redact(text)[1]:
            found.append(
                Rejection("secret_in_output", path[:200], "Output text may not contain a credential.")
            )
    return found[:MAX_REJECTIONS]


def raw_output(data: object) -> RawOutput:
    """What is kept of the output: the SHA-256 and size of its canonical JSON."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return RawOutput(hashlib.sha256(canonical).hexdigest(), len(canonical))
