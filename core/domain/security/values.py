"""Security values: how sensitive data is ordered, and what may never be shown.

**Sensitivity** comes only from what the architecture declares: ``data_classification`` (``public``
< ``internal`` < ``confidential`` < ``restricted``: the most sensitive data an element holds or
carries) and ``personal_data``. An element is sensitive when its classification is
``confidential`` or ``restricted`` or it declares personal data; not sensitive when its declared
classification is ``public`` or ``internal`` and it does not declare personal data; otherwise
**unknown** (``None``) — never assumed either way.

**Redaction**: a value whose name looks like a secret (``password``, ``token``, ``api_key``, …) is
never shown, in findings, evidence, responses or logs; only its name is (the IR diff's rule, shared:
typed properties with a closed set of values, like ``authorization`` or ``secret_source``, cannot
hold a secret and are shown).
"""

from core.architecture_ir.configuration import DATA_CLASSIFICATIONS, ConfigValue
from core.domain.redaction import REDACTED, is_secret_path, redacted, shows_a_secret

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")  # least to most sensitive
SENSITIVE_CLASSIFICATIONS = frozenset({"confidential", "restricted"})
assert set(CLASSIFICATIONS) == DATA_CLASSIFICATIONS  # noqa: S101 - one list, in order

__all__ = ["REDACTED", "is_secret_path", "redacted", "sensitivity", "shows_a_secret"]


def sensitivity(classification: ConfigValue | None, personal_data: ConfigValue | None) -> bool | None:
    """Whether data is sensitive by what is declared: True, False, or None (not established)."""
    if classification in SENSITIVE_CLASSIFICATIONS or personal_data is True:
        return True
    if classification in {"public", "internal"}:
        return False
    return None
