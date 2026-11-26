"""Small, predeclared owner-run local checks; no learning dataset or output persistence."""

import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt

from backend.api.schemas import ChatRequest, ChatResponse, ErrorResponse, GenerationOptions
from backend.broker.context import RoutingPlan, RoutingReason
from backend.broker.local_runtime import REAL_CONTEXT_TOKENS, RealLocalBroker
from backend.broker.rule_router import RuleRouter
from backend.broker.validators import (
    JsonField,
    JsonObjectSchema,
    JsonType,
    ResponseConstraints,
    ResponseFormat,
)
from backend.contracts import ContractModel, ExecutionMode, TaskCategory
from backend.model_management.initial_install import InitialInstallError
from backend.model_management.registry import HardwareContext, RegistrySnapshot
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.profiles.preferences import PreferenceProfile
from backend.versions import canonical_hash


class TargetModelRules(RuleRouter):
    """Synthetic harness target only; still ranks/checks eligibility using the rules router."""

    def __init__(self, model_id: str) -> None:
        super().__init__()
        self._target = model_id

    def rank(
        self,
        payload: SanitizedPayload,
        task: TaskCategory,
        snapshot: RegistrySnapshot,
        preferences: PreferenceProfile | None = None,
        hardware: HardwareContext | None = None,
    ) -> RoutingPlan:
        choices = self.rank_details(payload, task, snapshot, preferences, hardware)
        selected = next((item for item in choices if item.model_id == self._target), None)
        if selected is None:
            return RoutingPlan(
                model_ids=(), manifest_versions=(), reason=RoutingReason.NO_ELIGIBLE_MODEL
            )
        return RoutingPlan(
            model_ids=(selected.model_id,),
            manifest_versions=(selected.manifest_version,),
            reason=RoutingReason.RULES_RANKED,
        )


CASES = (
    (
        "arithmetic-v1",
        "Reply only with the integer result of 2 + 2.",
        TaskCategory.QA,
        ResponseConstraints(),
    ),
    (
        "synthetic-id-v1",
        "Repeat exactly this invented identifier: STU-1234-5678",
        TaskCategory.QA,
        ResponseConstraints(),
    ),
    (
        "json-ready-v1",
        "Return only a JSON object with the key status and the string value ready.",
        TaskCategory.FORMAT,
        ResponseConstraints(
            response_format=ResponseFormat.JSON,
            required_fields=("status",),
            json_schema=JsonObjectSchema(fields=(JsonField(name="status", kind=JsonType.STRING),)),
        ),
    ),
)


class SmokePlan(ContractModel):
    schema_version: Literal["local-smoke-v1"] = "local-smoke-v1"
    mode: Literal["real-smoke", "feasibility"]
    model_ids: tuple[str, ...] = Field(min_length=2, max_length=2)
    manifest_versions: tuple[str, ...] = Field(min_length=2, max_length=2)
    hardware_version: str
    endpoint: str
    runtime_version: str
    cases_version: str
    planned_calls: StrictInt = Field(gt=0, le=8)
    cloud_calls: Literal[0] = 0
    maximum_seconds: Literal[480] = 480
    context_tokens: Literal[8192] = REAL_CONTEXT_TOKENS

    @property
    def approval_digest(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace="local-smoke-approval-v1")


def smoke_plan(broker: RealLocalBroker, *, feasibility: bool) -> SmokePlan:
    snapshot = broker.registry.snapshot()
    identifiers = snapshot.eligible_model_ids[:2]
    if len(identifiers) != 2:
        raise InitialInstallError("two_verified_eligible_local_models_required")
    versions = {item.manifest.id: item.manifest.version for item in snapshot.roster.entries}
    return SmokePlan(
        mode="feasibility" if feasibility else "real-smoke",
        model_ids=identifiers,
        manifest_versions=tuple(versions[key] for key in identifiers),
        hardware_version=snapshot.hardware_version,
        endpoint=broker.dispatcher._target(identifiers[0]).endpoint,
        runtime_version=broker.monitor.runtime.version or "unknown",
        cases_version=canonical_hash(
            {
                "generation_options": GenerationOptions(max_output_tokens=64, seed=0).model_dump(
                    mode="json"
                ),
                "scoring": "exact-public-synthetic-v1",
                "cases": [
                    (key, text, task, constraint.model_dump(mode="json"))
                    for key, text, task, constraint in CASES
                ],
            },
            namespace="local-smoke-cases-v1",
        ),
        planned_calls=8 if feasibility else 4,
    )


class SmokeRow(ContractModel):
    model_id: str
    model_version: str | None = None
    manifest_version: str
    case_id: str
    repetition: StrictInt = Field(ge=0, le=1)
    status: Literal["success", "error", "stopped"]
    error_code: str | None = None
    resident_before: StrictBool | None = None
    elapsed_ms: float | None = Field(default=None, ge=0, strict=True)
    cold_observation_ms: float | None = Field(default=None, ge=0, strict=True)
    warm_observation_ms: float | None = Field(default=None, ge=0, strict=True)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    runtime_load_ms: float | None = Field(default=None, ge=0, strict=True)
    runtime_generation_ms: float | None = Field(default=None, ge=0, strict=True)
    output_tokens_per_second: float | None = Field(default=None, ge=0, strict=True)
    ttft_ms: None = None
    format_valid: StrictBool | None = None
    objective_quality: float | None = Field(default=None, ge=0, le=1, strict=True)
    available_ram_before: StrictInt | None = Field(default=None, ge=0)
    available_ram_after: StrictInt | None = Field(default=None, ge=0)
    resident_bytes_after: StrictInt | None = Field(default=None, ge=0)


class SmokeReport(ContractModel):
    schema_version: Literal["local-smoke-report-v1"] = "local-smoke-report-v1"
    evidence: Literal["real", "mocked_http"]
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    plan: SmokePlan
    rows: tuple[SmokeRow, ...]
    stopped: StrictBool
    stop_reason: str | None = None
    cloud_calls: Literal[0] = 0
    ttft_status: Literal["unavailable_non_streaming"] = "unavailable_non_streaming"
    scope: Literal["small_synthetic_checks_not_quality_benchmark"] = (
        "small_synthetic_checks_not_quality_benchmark"
    )


def objective_score(case_id: str, answer: str) -> float:
    if case_id == "arithmetic-v1":
        return float(answer.strip() == "4")
    if case_id == "synthetic-id-v1":
        return float(answer.strip() == "STU-1234-5678")
    try:
        return float(json.loads(answer) == {"status": "ready"})
    except ValueError:
        return 0.0


async def execute_smoke(
    broker: RealLocalBroker,
    plan: SmokePlan,
    approval: str,
    *,
    evidence: Literal["real", "mocked_http"] = "real",
) -> SmokeReport:
    import asyncio
    import secrets

    if not secrets.compare_digest(approval, plan.approval_digest):
        raise InitialInstallError("inference_not_authorized")
    current = smoke_plan(broker, feasibility=plan.mode == "feasibility")
    if current != plan:
        raise InitialInstallError("smoke_plan_changed")
    rows: list[SmokeRow] = []
    stopped = False
    try:
        async with asyncio.timeout(plan.maximum_seconds):
            for model_id, manifest_version in zip(
                plan.model_ids, plan.manifest_versions, strict=True
            ):
                broker._router = TargetModelRules(model_id)
                chosen = (CASES[0], CASES[2]) if plan.mode == "feasibility" else CASES[:2]
                for repetition in range(2 if plan.mode == "feasibility" else 1):
                    for case_id, prompt, task, constraints in chosen:
                        if not await broker.monitor.permitted(model_id):
                            rows.append(
                                SmokeRow(
                                    model_id=model_id,
                                    manifest_version=manifest_version,
                                    case_id=case_id,
                                    repetition=repetition,
                                    status="stopped",
                                    error_code="resources_or_runtime_unavailable",
                                )
                            )
                            return SmokeReport(
                                evidence=evidence,
                                plan=plan,
                                rows=tuple(rows),
                                stopped=True,
                                stop_reason="resources_or_runtime_unavailable",
                            )
                        before = broker.monitor.hardware.available_ram_bytes
                        manifest = next(
                            item.manifest
                            for item in broker.registry.snapshot().roster.entries
                            if item.manifest.id == model_id
                        )
                        resident = next(
                            item.resident
                            for item in broker.monitor.runtime.inventory.models
                            if item.runtime_tag == manifest.runtime_tag
                        )
                        result = await broker.chat(
                            ChatRequest(
                                prompt=prompt,
                                mode=ExecutionMode.LOCAL_ONLY,
                                task_hint=task,
                                constraints=constraints,
                                options=GenerationOptions(max_output_tokens=64, seed=0),
                            )
                        )
                        await broker.monitor.refresh()
                        values = dict(
                            model_id=model_id,
                            manifest_version=manifest_version,
                            case_id=case_id,
                            repetition=repetition,
                            resident_before=resident,
                            available_ram_before=before,
                            available_ram_after=broker.monitor.hardware.available_ram_bytes,
                            resident_bytes_after=broker.monitor.runtime.resident_bytes,
                        )
                        if (
                            isinstance(result, ChatResponse)
                            and result.model_id == model_id
                            and result.route == "local"
                            and not result.mock
                        ):
                            metrics = result.generation_metrics
                            rows.append(
                                SmokeRow.model_validate(
                                    {
                                        **values,
                                        "status": "success",
                                        "model_version": result.model_version,
                                        "elapsed_ms": result.timings.total_ms,
                                        "cold_observation_ms": result.timings.total_ms
                                        if resident is False
                                        else None,
                                        "warm_observation_ms": result.timings.total_ms
                                        if resident is True
                                        else None,
                                        "input_tokens": metrics.input_tokens if metrics else None,
                                        "output_tokens": metrics.output_tokens if metrics else None,
                                        "runtime_load_ms": metrics.load_ms if metrics else None,
                                        "runtime_generation_ms": metrics.generation_ms
                                        if metrics
                                        else None,
                                        "output_tokens_per_second": metrics.output_tokens_per_second
                                        if metrics
                                        else None,
                                        "format_valid": True,
                                        "objective_quality": objective_score(
                                            case_id, result.answer
                                        ),
                                    }
                                )
                            )
                        else:
                            code = (
                                result.error_code.value
                                if isinstance(result, ErrorResponse)
                                else "unexpected_nonlocal_result"
                            )
                            rows.append(
                                SmokeRow.model_validate(
                                    {
                                        **values,
                                        "status": "error",
                                        "error_code": code,
                                        "format_valid": False
                                        if code == "validation_failed"
                                        else None,
                                    }
                                )
                            )
                            if code in {
                                "timeout",
                                "unavailable",
                                "privacy_failed",
                                "output_blocked",
                            }:
                                return SmokeReport(
                                    evidence=evidence,
                                    plan=plan,
                                    rows=tuple(rows),
                                    stopped=True,
                                    stop_reason=code,
                                )
                        if (
                            broker.monitor.hardware.pressure != "normal"
                            or not broker.monitor.runtime.available
                        ):
                            return SmokeReport(
                                evidence=evidence,
                                plan=plan,
                                rows=tuple(rows),
                                stopped=True,
                                stop_reason="post_request_resources_or_runtime_unavailable",
                            )
    except TimeoutError:
        stopped = True
    return SmokeReport(
        evidence=evidence,
        plan=plan,
        rows=tuple(rows),
        stopped=stopped,
        stop_reason="overall_deadline" if stopped else None,
    )
