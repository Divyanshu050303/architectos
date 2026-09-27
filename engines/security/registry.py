"""The security analyzers this version ships, in order (an analyzer building on others comes after
them). Each phase of the milestone adds its analyzers here."""

from .authentication import Authentication
from .authorization import Authorization
from .encryption import Encryption
from .engine import Registry
from .pii import DataProtection
from .trust_boundaries import TrustBoundaries


def default_registry() -> Registry:
    return Registry([TrustBoundaries(), Authentication(), Authorization(), Encryption(), DataProtection()])
