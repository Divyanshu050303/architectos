"""Operational metrics, as the domain sees them: counters and observations with a few labels.

Labels are identifiers (``provider=anthropic``, ``reason=llm_timeout``), never user content: a
label value that is not a short identifier is refused, so raw requirement text can never end up in
a metric. Implementations decide where metrics go (structured logs today, an exporter later).
"""

import re
from typing import Protocol

_LABEL = re.compile(r"^[a-z0-9_.:/#-]{1,64}$")


def check_labels(labels: dict[str, str]) -> None:
    for key, value in labels.items():
        if not _LABEL.fullmatch(key) or not _LABEL.fullmatch(value):
            raise ValueError(f"metric label {key!r} is not a short identifier")


class Metrics(Protocol):
    def increment(self, name: str, value: int = 1, **labels: str) -> None: ...

    def observe(self, name: str, value: float, **labels: str) -> None: ...


class NullMetrics:
    """For contexts without metrics (tests, scripts): checks labels, records nothing."""

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        check_labels(labels)

    def observe(self, name: str, value: float, **labels: str) -> None:
        check_labels(labels)
