"""Request-only cloud resume state; never serialize or persist this object."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, SupportsIndex

from backend.api.schemas import ChatResponse, ResponseTimings
from backend.broker.context import RequestContext
from backend.broker.policy_gate import CloudProposal
from backend.broker.validators import ResponseConstraints
from backend.contracts import TaskCategory
from backend.model_management.registry import RosterLease

if TYPE_CHECKING:
    from backend.broker.orchestrator import DispatchSession


@dataclass(slots=True, repr=False)
class PendingChat:
    session: "DispatchSession"
    context: RequestContext
    proposal: CloudProposal | None
    constraints: ResponseConstraints
    task: TaskCategory
    timings: ResponseTimings
    cloud_model_id: str
    lease: RosterLease | None = None
    best_local: ChatResponse | None = None
    timer: asyncio.TimerHandle | None = None

    @property
    def cloud_proposal(self) -> CloudProposal:
        if self.proposal is None:
            raise ValueError("This request is not awaiting cloud approval")
        return self.proposal

    def __repr__(self) -> str:
        return "PendingChat(<redacted>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("pending request serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("pending request serialization is forbidden")

    def model_dump(self) -> NoReturn:
        raise TypeError("pending request serialization is forbidden")
