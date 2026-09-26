"""The capacity models this ArchitectOS ships, in one registry. Adding a model: write it in this
package and list it here; a changed model gets a new version (which changes the model-set version).
Memory is deliberately not modeled: what a request holds depends on concurrency and runtime, and no
declared figure supports a formula yet."""

from . import bandwidth, compute, concurrency, rps, storage
from .engine import Registry


def default_registry() -> Registry:
    """A new registry each time: a registry can still be registered into, so none is shared."""
    return Registry([*rps.MODELS, *compute.MODELS, *concurrency.MODELS, *storage.MODELS, *bandwidth.MODELS])
