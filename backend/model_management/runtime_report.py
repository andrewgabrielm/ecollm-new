"""Read-only runtime metadata contracts; no model health warm-up or generation."""

from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.contracts import ContractModel
from backend.model_management.registry import RuntimeInventory


class RuntimeReport(ContractModel):
    schema_version: Literal["ollama-report-v1"] = "ollama-report-v1"
    available: StrictBool = False
    version: str | None = Field(
        default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+_-]{0,48})$"
    )
    inventory: RuntimeInventory = Field(default_factory=lambda: RuntimeInventory(available=False))
    installed_count: StrictInt = Field(default=0, ge=0)
    resident_count: StrictInt | None = Field(default=None, ge=0)
    resident_bytes: StrictInt | None = Field(default=None, ge=0)
    reason: Literal["available", "runtime_unavailable", "invalid_metadata"] = "runtime_unavailable"

    @model_validator(mode="after")
    def check_report(self) -> "RuntimeReport":
        if self.available != self.inventory.available or self.available != (
            self.reason == "available"
        ):
            raise ValueError("Runtime status and inventory disagree")
        if self.available and self.version is None:
            raise ValueError("Available runtime requires an observed version")
        return self
