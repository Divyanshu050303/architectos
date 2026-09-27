"""The security analyzers this version ships, in order (an analyzer building on others comes after
them). Each phase of the milestone adds its analyzers here."""

from .authentication import Authentication
from .authorization import Authorization
from .encryption import Encryption
from .engine import Registry
from .exposure import Exposure
from .pii import DataProtection
from .policy import Policy
from .requirements import Requirements
from .secrets import Secrets
from .threat_model import ThreatModel
from .trust_boundaries import TrustBoundaries


def default_registry() -> Registry:
    return Registry(
        [
            TrustBoundaries(),
            Authentication(),
            Authorization(),
            Encryption(),
            DataProtection(),
            Secrets(),
            Exposure(),
            ThreatModel(),  # builds on the findings of those before it
            Policy(),
            Requirements(),
        ]
    )
