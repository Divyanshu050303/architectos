"""The rules this ArchitectOS ships, in one registry. Adding a rule: write it in ``rules/`` and
list it here; a changed rule gets a new version (which changes the rule-set version)."""

from .engine import Registry
from .rules import completeness, configuration, consistency, policy


def default_registry() -> Registry:
    """A new registry each time: a registry can still be registered into, so none is shared."""
    return Registry([*consistency.RULES, *completeness.RULES, *configuration.RULES, *policy.RULES])
