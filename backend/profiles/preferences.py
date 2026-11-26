"""Consent and preference contracts only; persistence/onboarding arrive later."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from backend.contracts import ContractModel, ExecutionMode, TaskCategory
from backend.model_management.manifest import Identifier, PositiveBytes


class PortfolioMode(StrEnum):
    MANUAL = "manual"
    SUGGEST = "suggest"


class RuleWeights(ContractModel):
    quality: Annotated[float, Field(ge=0, strict=True)] = 1.0
    latency: Annotated[float, Field(ge=0, strict=True)] = 0.3
    load: Annotated[float, Field(ge=0, strict=True)] = 0.1

    @model_validator(mode="after")
    def check_weights(self) -> Self:
        if self.quality + self.latency + self.load == 0:
            raise ValueError("At least one ranking weight must be positive")
        return self


class PreferenceProfile(ContractModel):
    profile_id: Identifier = "local"
    mode: ExecutionMode = ExecutionMode.LOCAL_ONLY
    task_interests: tuple[TaskCategory, ...] = ()
    weights: RuleWeights = Field(default_factory=RuleWeights)
    model_disk_budget_bytes: PositiveBytes | None = None
    portfolio_mode: PortfolioMode = PortfolioMode.MANUAL
    exact_cache_enabled: StrictBool = False
    semantic_cache_enabled: StrictBool = False
    workload_persistence_enabled: StrictBool = False
    local_learning_enabled: StrictBool = False
    persistent_analytics_enabled: StrictBool = False

    @model_validator(mode="after")
    def check_interests(self) -> Self:
        if len(set(self.task_interests)) != len(self.task_interests):
            raise ValueError("Duplicate task interests")
        return self
