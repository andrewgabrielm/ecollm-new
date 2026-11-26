"""Pure ranking contract; routers never authorize or invoke generation."""

from typing import Literal, Protocol

from backend.broker.context import RoutingPlan
from backend.contracts import TaskCategory
from backend.model_management.registry import HardwareContext, RegistrySnapshot
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.profiles.preferences import PreferenceProfile


class RoutingError(ValueError):
    """Content-free routing/configuration failure."""


class Router(Protocol):
    @property
    def router_type(self) -> Literal["rules", "lightgbm"]: ...
    @property
    def router_version(self) -> str: ...
    def rank(
        self,
        payload: SanitizedPayload,
        task: TaskCategory,
        snapshot: RegistrySnapshot,
        preferences: PreferenceProfile | None = None,
        hardware: HardwareContext | None = None,
    ) -> RoutingPlan: ...
