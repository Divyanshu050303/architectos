"""What the discovery service asks of the discovery engine: a result for a request it has authorized,
the proposal of a stored result under review decisions, and the versions of the extractors and rules
it uses (a result records them, so a proposal is reproduced from the same versions)."""

from collections.abc import Mapping
from typing import Protocol

from .results import DiscoveryResult, Proposal
from .runs import DiscoveryRequest, ReviewDecision


class DiscoveryEngine(Protocol):
    def discover(self, request: DiscoveryRequest, name: str | None = None) -> DiscoveryResult:
        """Every artifact read, normalized, mapped and related, and the proposal without decisions.
        Never executes, evaluates or fetches anything; never writes an architecture."""
        ...

    def propose(
        self, result: DiscoveryResult, decisions: tuple[ReviewDecision, ...], name: str | None = None
    ) -> Proposal:
        """The proposal of a stored result under these decisions (history: the latest per subject)."""
        ...

    def versions(self) -> Mapping[str, int]:
        """The version of every extractor and rule the engine uses now."""
        ...
