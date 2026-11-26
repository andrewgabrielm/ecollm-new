"""Extension interfaces; cache enabled in Step 10, others remain disabled."""

from typing import Literal, Protocol

from pydantic import StrictBool

from backend.contracts import ContractModel


class CacheBoundary(Protocol):
    """A later implementation may supply a scoped lookup after routing."""

    @property
    def enabled(self) -> bool: ...


class PersistenceBoundary(Protocol):
    """A later opt-in implementation may accept allowlisted metadata only."""

    @property
    def enabled(self) -> bool: ...


class LearnedRoutingBoundary(Protocol):
    """The existing Router contract remains the extension seam for learned ranking."""

    @property
    def enabled(self) -> bool: ...


class Extensions(ContractModel):
    cache_enabled: StrictBool = False
    persistence_enabled: Literal[False] = False
    learned_router_enabled: Literal[False] = False


DisabledExtensions = Extensions
