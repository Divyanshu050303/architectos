"""What may never be shown: a value whose name looks like a secret (``password``, ``token``,
``api_key``, …) — the IR diff's rule, shared by every engine that reports configuration evidence.
Typed properties with a closed set of values (``authorization``, ``secret_source``) cannot hold a
secret and are shown; a preserved key is tested whole, dots included."""

from collections.abc import Iterable

from core.architecture_ir.diff import REDACTED, is_secret_path
from core.domain.engine_results import Evidence

__all__ = ["REDACTED", "is_secret_path", "redacted", "shows_a_secret"]


def redacted(label: str, value: str) -> Evidence:
    """Evidence for ``label``, its value replaced when the label names a secret."""
    return Evidence(label, REDACTED if is_secret_path(label) else value)


def shows_a_secret(evidence: Iterable[Evidence]) -> bool:
    """Whether any evidence would show the value of a secret-looking setting."""
    return any(is_secret_path(e.label) and e.value != REDACTED for e in evidence)
