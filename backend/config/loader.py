"""Validated local YAML and environment-only secrets; no runtime/network side effects."""

from collections.abc import Callable, Hashable, Mapping
from os import environ
from pathlib import Path
from typing import Annotated, Any, Self, TypeVar, cast

import yaml
from pydantic import (
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from backend.api.schemas import CloudFallbackReason
from backend.contracts import ContractModel, ExecutionMode, TaskCategory
from backend.model_management.manifest import ModelCatalog
from backend.profiles.preferences import PreferenceProfile, RuleWeights
from backend.versions import POLICY_VERSION, canonical_hash

CONFIG_ROOT = Path(__file__).resolve().parent
T = TypeVar("T", bound=ContractModel)


class ConfigError(ValueError):
    """Safe diagnostics only: never include config contents or validation input."""


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Hashable, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise ConfigError("Expected YAML mapping")
        result: dict[Hashable, Any] = {}
        # PyYAML's constructor lacks annotations; constrain its result at this boundary.
        construct = cast(Callable[[yaml.Node, bool], object], self.construct_object)
        for key_node, value_node in node.value:
            key = construct(key_node, deep)
            if not isinstance(key, str) or key in result:
                raise ConfigError("YAML keys must be unique strings")
            result[key] = construct(value_node, deep)
        return result


def load_yaml(path: Path, contract: type[T]) -> T:
    """Reject ambiguous keys/aliases and report errors without exposing source lines."""
    try:
        with path.open("rb") as source:
            contents = source.read(65_537)
        if len(contents) > 65_536:
            raise ConfigError("Configuration exceeds size limit")
        if any(
            isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(contents)
        ):
            raise ConfigError("YAML aliases and anchors are not supported")
        values = yaml.load(contents, Loader=_UniqueKeyLoader)
        return contract.model_validate(values)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError):
        raise ConfigError("Configuration could not be read or validated") from None


class ComputeBudgets(ContractModel):
    local_seconds: float = Field(default=60, gt=0, le=120, strict=True)
    cloud_seconds: float = Field(default=30, gt=0, le=120, strict=True)
    overall_seconds: float = Field(default=120, gt=0, le=120, strict=True)
    approval_seconds: float = Field(default=120, gt=0, le=120, strict=True)
    resume_seconds: float = Field(default=120, gt=0, le=120, strict=True)
    health_ttl_seconds: float = Field(default=10, gt=0, le=60, strict=True)
    local_connection_retries: Annotated[StrictInt, Field(ge=0, le=1)] = 1
    cloud_retries: Annotated[StrictInt, Field(ge=0, le=0)] = 0

    @model_validator(mode="after")
    def check_compute_bounds(self) -> Self:
        if max(self.local_seconds, self.cloud_seconds, self.resume_seconds) > self.overall_seconds:
            raise ValueError("Per-dispatch and resume budgets cannot exceed overall budget")
        return self


class PolicyConfig(ContractModel):
    schema_version: StrictStr = "policy-config-v1"
    policy_version: StrictStr = POLICY_VERSION
    hard_block_entity_types: tuple[
        Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z_]{0,63}$")], ...
    ] = ("CREDIT_CARD", "US_SSN")
    allowed_fallback_reasons: tuple[CloudFallbackReason, ...] = (
        CloudFallbackReason.LOCAL_ERROR,
        CloudFallbackReason.VALIDATION_FAILURE,
    )
    allow_no_local_model_cloud: StrictBool = False
    cloud_preferred_categories: tuple[TaskCategory, ...] = ()

    @model_validator(mode="after")
    def check_policy(self) -> Self:
        if self.schema_version != "policy-config-v1" or self.policy_version != POLICY_VERSION:
            raise ValueError("Unsupported policy configuration version")
        for sequence in (
            self.hard_block_entity_types,
            self.allowed_fallback_reasons,
            self.cloud_preferred_categories,
        ):
            if len(sequence) != len(set(sequence)):
                raise ValueError("Duplicate policy values")
        if (
            CloudFallbackReason.NO_LOCAL_MODEL in self.allowed_fallback_reasons
        ) != self.allow_no_local_model_cloud:
            raise ValueError("No-model cloud reason requires explicit matching opt-in")
        if (CloudFallbackReason.CLOUD_PREFERRED in self.allowed_fallback_reasons) != bool(
            self.cloud_preferred_categories
        ):
            raise ValueError("Cloud-preferred reason requires explicit matching categories")
        return self

    @property
    def version(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace=POLICY_VERSION)


class CloudDestinationConfig(ContractModel):
    """Owner-declared cloud route. Validation delegated to CloudDestination at wire-up.

    ``api_key_variable`` is optional. When omitted the destination has no
    environment-provided key; the user supplies one at runtime via the
    ``/api/cloud/{model_id}/key`` route and the key lives in process memory
    only.
    """

    endpoint: StrictStr
    health_endpoint: StrictStr | None = None
    model_id: Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
    model_version: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    display_name: Annotated[StrictStr, Field(min_length=1, max_length=64)] = "Cloud provider"
    runtime_model: (
        Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")]
        | None
    ) = None
    api_key_variable: Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] | None = None


class AppConfig(ContractModel):
    schema_version: StrictStr = "settings-v1"
    mock_mode: StrictBool = True
    initial_mode: ExecutionMode = ExecutionMode.LOCAL_ONLY
    managed_model_limit: Annotated[StrictInt, Field(ge=2, le=3)] = 3
    initial_model_count: Annotated[StrictInt, Field(ge=2, le=2)] = 2
    budgets: ComputeBudgets = Field(default_factory=ComputeBudgets)
    rule_weights: RuleWeights = Field(default_factory=RuleWeights)
    preferences: PreferenceProfile = Field(default_factory=PreferenceProfile)
    cloud_destinations: tuple[CloudDestinationConfig, ...] = ()

    @model_validator(mode="after")
    def check_schema(self) -> Self:
        if self.schema_version != "settings-v1":
            raise ValueError("Unsupported settings schema")
        return self

    @property
    def version(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace="settings-v1")


class Settings(AppConfig):
    cloud_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)


def load_settings(
    environment: Mapping[str, str] | None = None, *, config_path: Path | None = None
) -> Settings:
    """Environment may override mock mode and supply secrets; no implicit .env reads."""
    source = environ if environment is None else environment
    config = load_yaml(config_path or CONFIG_ROOT / "settings.yaml", AppConfig)
    raw_mode = source.get("MOCK_MODE", "1" if config.mock_mode else "0")
    if raw_mode not in {"0", "1"}:
        raise ConfigError("MOCK_MODE must be 0 or 1")
    return Settings(
        **{**config.model_dump(), "mock_mode": raw_mode == "1"},
        cloud_api_key=SecretStr(source["CLOUD_API_KEY"]) if source.get("CLOUD_API_KEY") else None,
    )


def load_catalog(path: Path | None = None) -> ModelCatalog:
    return load_yaml(path or CONFIG_ROOT / "catalog.yaml", ModelCatalog)


def load_policy(path: Path | None = None) -> PolicyConfig:
    return load_yaml(path or CONFIG_ROOT / "policy.yaml", PolicyConfig)
