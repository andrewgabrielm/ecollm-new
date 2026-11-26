"""Dated optional pricing; unknown prices or token counts remain unavailable."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from backend.api.schemas import Identifier, Version
from backend.config.loader import CONFIG_ROOT, load_yaml
from backend.contracts import ContractModel
from backend.model_management.manifest import ManifestSource
from backend.providers.base import TokenMetrics

Rate = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=9)]


class ModelPrice(ContractModel):
    model_id: Identifier
    model_version: Version
    input_usd_per_million: Rate
    output_usd_per_million: Rate
    source: ManifestSource


class PricingCatalog(ContractModel):
    schema_version: Literal["pricing-v1"] = "pricing-v1"
    as_of: date
    currency: Literal["USD"] = "USD"
    rates: tuple[ModelPrice, ...] = ()

    @model_validator(mode="after")
    def unique_versions(self) -> Self:
        if len({(rate.model_id, rate.model_version) for rate in self.rates}) != len(self.rates):
            raise ValueError("Duplicate model pricing version")
        return self

    def estimate(
        self, model_id: str, model_version: str, tokens: TokenMetrics | None
    ) -> Decimal | None:
        if tokens is None or tokens.input_tokens is None or tokens.output_tokens is None:
            return None
        for rate in self.rates:
            if (rate.model_id, rate.model_version) == (model_id, model_version):
                return (
                    rate.input_usd_per_million * tokens.input_tokens
                    + rate.output_usd_per_million * tokens.output_tokens
                ) / Decimal(1_000_000)
        return None


def load_pricing(path: Path | None = None) -> PricingCatalog:
    return load_yaml(path or CONFIG_ROOT / "pricing.yaml", PricingCatalog)
