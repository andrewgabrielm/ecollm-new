"""Request-local private state and immutable policy-independent routing plans."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID

from pydantic import Field, StrictStr, model_validator

from backend.api.schemas import CloudFallbackReason, Identifier, Version
from backend.contracts import ContractModel
from backend.privacy.placeholder_map import PlaceholderMap
from backend.privacy.sanitized_payload import SanitizedPayload


class RoutingReason(StrEnum):
    RULES_RANKED = "rules_ranked"
    LEARNED_RANKED = "learned_ranked"
    RULES_FALLBACK = "rules_fallback"
    LEARNED_FALLBACK = "learned_fallback"
    NO_ELIGIBLE_MODEL = "no_eligible_model"


class RouterType(StrEnum):
    RULES = "rules"
    LIGHTGBM = "lightgbm"


class RoutingPlan(ContractModel):
    model_ids: tuple[Identifier, ...] = Field(max_length=2)
    manifest_versions: tuple[Version, ...] = Field(max_length=2)
    reason: RoutingReason
    cloud_hint: CloudFallbackReason | None = None
    router_type: RouterType = RouterType.RULES
    router_version: StrictStr | None = None

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        if len(self.model_ids) != len(self.manifest_versions):
            raise ValueError("routing model/version lengths differ")
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError("routing candidates must be unique")
        if not self.model_ids and self.reason != RoutingReason.NO_ELIGIBLE_MODEL:
            raise ValueError("empty routing plan requires no-eligible-model reason")
        if self.model_ids and self.reason == RoutingReason.NO_ELIGIBLE_MODEL:
            raise ValueError("no-eligible-model reason cannot contain candidates")
        return self


class RequestContext:
    """In-memory only: no instance dictionary, dataclass serialization or pickle.

    This container is deliberately separate from transport/config models. Callers
    must never log its properties. Placeholder handling itself belongs to Step 03.
    """

    __slots__ = (
        "_placeholder_map",
        "_privacy_placeholders",
        "_raw_prompt",
        "_request_id",
        "_sanitized_payload",
    )

    def __init__(
        self,
        request_id: UUID,
        raw_prompt: str,
        placeholder_map: Mapping[str, str] | None = None,
        sanitized_payload: SanitizedPayload | None = None,
        *,
        privacy_placeholders: PlaceholderMap | None = None,
    ) -> None:
        if sanitized_payload is not None and sanitized_payload.request_id != request_id:
            raise ValueError("request context and sanitized payload IDs differ")
        if privacy_placeholders is not None and privacy_placeholders.request_id != request_id:
            raise ValueError("request context and placeholder map IDs differ")
        if privacy_placeholders is not None and placeholder_map is not None:
            raise ValueError("request context cannot hold two placeholder maps")
        self._request_id = request_id
        self._raw_prompt = raw_prompt
        self._placeholder_map = dict(placeholder_map or {})
        self._sanitized_payload = sanitized_payload
        self._privacy_placeholders = privacy_placeholders

    @property
    def request_id(self) -> UUID:
        return self._request_id

    @property
    def raw_prompt(self) -> str:
        return self._raw_prompt

    @property
    def placeholder_map(self) -> Mapping[str, str]:
        return MappingProxyType(self._placeholder_map)

    @property
    def sanitized_payload(self) -> SanitizedPayload | None:
        return self._sanitized_payload

    @property
    def privacy_placeholders(self) -> PlaceholderMap | None:
        return self._privacy_placeholders

    def clear(self) -> None:
        self._raw_prompt = ""
        self._placeholder_map.clear()
        self._sanitized_payload = None
        if self._privacy_placeholders is not None:
            self._privacy_placeholders.clear()
            self._privacy_placeholders = None

    def __repr__(self) -> str:
        return "RequestContext(<redacted>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("request context serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("request context serialization is forbidden")

    def model_dump(self) -> NoReturn:
        raise TypeError("request context serialization is forbidden")

    def model_dump_json(self) -> NoReturn:
        raise TypeError("request context serialization is forbidden")
