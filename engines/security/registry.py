"""The security analyzers this version ships, in order (an analyzer building on others comes after
them). Each phase of the milestone adds its analyzers here."""

from .engine import Registry


def default_registry() -> Registry:
    return Registry(())
