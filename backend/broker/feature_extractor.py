"""One pre-generation feature schema for both benchmark and inference callers.

Token length is a deterministic lexical estimate, not a runtime tokenizer count.
Incidental placeholder namespaces are normalized before structural counts.
No source text, token strings, entity values, request IDs, answers or outcome
metrics enter this row. Missing historical observations remain unknown.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.contracts import ContractModel, TaskCategory
from backend.model_management.manifest import (
    BenchmarkProvenance,
    CapabilityBenchmark,
    Digest,
    Identifier,
    Platform,
    RosterEntry,
)
from backend.model_management.registry import HardwareContext
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.versions import canonical_hash

FEATURE_SCHEMA_VERSION: Literal["routing-features-v1"] = "routing-features-v1"
FEATURE_SCHEMA_HASH: str
Count = Annotated[StrictInt, Field(ge=0)]


class FeatureError(ValueError):
    """No caller values are embedded in feature-extraction errors."""


class FeatureRow(ContractModel):
    schema_version: Literal["routing-features-v1"] = FEATURE_SCHEMA_VERSION
    schema_hash: Digest = Field(default_factory=lambda: FEATURE_SCHEMA_HASH)
    task: TaskCategory
    sanitized_char_count: Count
    sanitized_token_estimate: Count
    line_count: Count
    question_count: Count
    digit_count: Count
    bullet_line_count: Count
    bracket_count: Count
    has_json_cue: StrictBool
    has_code_fence: StrictBool
    candidate_id: Identifier
    candidate_manifest_version: Digest
    candidate_weight_digest: Digest | None
    candidate_family: StrictStr
    candidate_quantization: StrictStr
    capability_quality: float | None = Field(default=None, ge=0, le=1, strict=True)
    capability_latency_ms: float | None = Field(default=None, ge=0, strict=True)
    capability_provenance: BenchmarkProvenance
    capability_benchmark_version: Digest | None
    installed: StrictBool
    healthy: StrictBool
    resident: StrictBool | None
    hardware_platform: Platform | None
    hardware_ram_bytes: Count | None
    hardware_vram_bytes: Count | None
    hardware_cpu_threads: Count | None
    hardware_version: Digest

    @model_validator(mode="after")
    def check_schema(self) -> Self:
        if self.schema_hash != FEATURE_SCHEMA_HASH:
            raise ValueError("Unsupported feature schema")
        return self


FEATURE_SCHEMA_HASH = canonical_hash(
    FeatureRow.model_json_schema(), namespace=FEATURE_SCHEMA_VERSION
)


def trusted_payload(payload: SanitizedPayload) -> SanitizedPayload:
    """Validate the in-memory seal and copied/constructed values without echoing inputs."""
    try:
        if not isinstance(payload, SanitizedPayload) or not payload.is_trusted:
            raise FeatureError("Feature extraction requires a trusted sanitized payload")
        SanitizedPayload.model_validate(payload.model_dump())
        return payload
    except (AttributeError, TypeError, ValueError):
        raise FeatureError("Feature extraction requires a trusted sanitized payload") from None


def capability_for(
    entry: RosterEntry, task: TaskCategory, *, mock_mode: bool
) -> CapabilityBenchmark | None:
    """Ignore synthetic prior observations outside an explicitly mocked roster."""
    for benchmark in entry.manifest.capability_benchmark:
        if benchmark.task == task and (
            benchmark.provenance == BenchmarkProvenance.MEASURED
            or (mock_mode and benchmark.provenance == BenchmarkProvenance.SYNTHETIC)
        ):
            return benchmark
    return None


def extract_features(
    payload: SanitizedPayload,
    task: TaskCategory,
    candidate: RosterEntry,
    hardware: HardwareContext | None = None,
    *,
    mock_mode: bool = False,
) -> FeatureRow:
    """Return the same immutable, versioned input row in training and live routing."""
    try:
        trusted_payload(payload)
        if not isinstance(task, TaskCategory) or not isinstance(mock_mode, bool):
            raise FeatureError("Invalid feature context")
        entry = RosterEntry.model_validate(candidate.model_dump(warnings=False))
        machine = HardwareContext.model_validate(
            (hardware or HardwareContext()).model_dump(warnings=False)
        )
        benchmark = capability_for(entry, task, mock_mode=mock_mode)
        text = re.sub(
            r"\[\[PII_[a-f0-9]{32}_[A-Z][A-Z_]{0,63}_[1-9][0-9]*\]\]",
            "MASKED_ENTITY",
            payload.text,
        )
        return FeatureRow(
            task=task,
            sanitized_char_count=len(payload.text),
            sanitized_token_estimate=len(re.findall(r"\w+|[^\w\s]", text)),
            line_count=len(text.splitlines()),
            question_count=text.count("?"),
            digit_count=sum(character.isdigit() for character in text),
            bullet_line_count=len(re.findall(r"(?m)^\s*(?:[-*+] |\d+[.)] )", text)),
            bracket_count=sum(text.count(character) for character in "[]{}()"),
            has_json_cue=bool(re.search(r"\bjson\b|^\s*[\[{]", text, re.IGNORECASE)),
            has_code_fence="```" in text,
            candidate_id=entry.manifest.id,
            candidate_manifest_version=entry.manifest.version,
            candidate_weight_digest=entry.manifest.pinned_digest,
            candidate_family=entry.manifest.family,
            candidate_quantization=entry.manifest.quantization,
            capability_quality=benchmark.quality if benchmark else None,
            capability_latency_ms=benchmark.latency_ms if benchmark else None,
            capability_provenance=benchmark.provenance
            if benchmark
            else BenchmarkProvenance.UNKNOWN,
            capability_benchmark_version=benchmark.benchmark_version if benchmark else None,
            installed=entry.installed,
            healthy=entry.healthy,
            resident=entry.resident,
            hardware_platform=machine.platform,
            hardware_ram_bytes=machine.ram_bytes,
            hardware_vram_bytes=machine.vram_bytes,
            hardware_cpu_threads=machine.cpu_threads,
            hardware_version=machine.version,
        )
    except (AttributeError, TypeError, ValueError):
        raise FeatureError("Feature extraction failed") from None
