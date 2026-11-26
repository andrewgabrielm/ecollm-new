"""Step 12 gated evaluation dispatch.

The :class:`EvaluationDriver` forces a single named candidate, bypasses the
cache/cloud/cascade paths and still routes every dispatch through the policy
gate inside :class:`~backend.broker.orchestrator.GuardedDispatcher`.  The
driver returns one :class:`EvaluationResult` per call; scoring and persistence
happen in the collector so this module stays free of benchmark coupling.
"""

from __future__ import annotations

import re
from time import perf_counter
from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

from backend.api.schemas import GenerationOptions
from backend.broker.context import RequestContext
from backend.broker.feature_extractor import FeatureRow, extract_features
from backend.broker.orchestrator import GuardedDispatcher
from backend.contracts import ContractModel, ErrorCode, ExecutionMode, TaskCategory
from backend.learning.collection.types import ObservationStatus
from backend.model_management.registry import HardwareContext, ModelRegistry
from backend.privacy.analyzer import Analyzer
from backend.privacy.inspector import OutputInspector
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import ProviderFailure, ProviderSuccess
from backend.versions import POLICY_VERSION, canonical_hash

Identifier = StrictStr
Version = StrictStr
Digest = StrictStr
_HASH_NAMESPACE = "observations-feature-v1"


class EvaluationError(RuntimeError):
    """Raised for structural misuse of the driver; distinct from runtime failures."""


class EvaluationResult(ContractModel):
    schema_version: Literal["evaluation-v2"] = "evaluation-v2"
    status: ObservationStatus
    model_id: Identifier
    model_version: Version
    task: TaskCategory
    feature_hash: Digest = Field(pattern=r"^[a-f0-9]{64}$")
    features: FeatureRow | None = None
    answer: StrictStr | None = Field(default=None, max_length=64000)
    error_code: ErrorCode | None = None
    total_ms: StrictFloat = Field(ge=0.0)
    privacy_ms: StrictFloat = Field(ge=0.0)
    routing_ms: StrictFloat = Field(ge=0.0)
    generation_ms: StrictFloat = Field(ge=0.0)
    inspection_ms: StrictFloat = Field(ge=0.0)
    load_ms: StrictFloat | None = Field(default=None, ge=0.0)
    prompt_ms: StrictFloat | None = Field(default=None, ge=0.0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    resident: StrictBool
    policy_version: Version
    mock: StrictBool


class EvaluationDriver:
    """Single-candidate evaluation path; no cache, no cloud, no cascade.

    Each call fully isolates its state: the :class:`RequestContext` and the
    sanitizer placeholders live only in the stack frame below ``evaluate``.
    """

    def __init__(
        self,
        dispatcher: GuardedDispatcher,
        registry: ModelRegistry,
        analyzer: Analyzer,
        *,
        inspector: OutputInspector | None = None,
        mode: Literal["mock", "injected", "real"] = "injected",
    ) -> None:
        self._dispatcher = dispatcher
        self._registry = registry
        self._analyzer = analyzer
        self._sanitizer = Sanitizer(analyzer)
        self._inspector = inspector or OutputInspector(analyzer)
        self._mode = mode

    @property
    def mock(self) -> bool:
        return self._mode == "mock"

    async def evaluate(
        self,
        prompt: str,
        model_id: str,
        *,
        task: TaskCategory,
        options: GenerationOptions | None = None,
        hardware: HardwareContext | None = None,
    ) -> EvaluationResult:
        if not isinstance(task, TaskCategory):
            raise EvaluationError("task must be a TaskCategory member")
        if not prompt or not isinstance(prompt, str):
            raise EvaluationError("prompt is required")
        options = options or GenerationOptions()
        started = perf_counter()
        request_id = uuid4()

        # Sanitize — a sanitizer failure aborts before any generation is reached.
        privacy_started = perf_counter()
        try:
            sanitized = self._sanitizer.sanitize(request_id, prompt)
        except Exception:
            return _early_failure(
                status=ObservationStatus.PRIVACY_FAILURE,
                error_code=ErrorCode.PRIVACY_FAILED,
                model_id=model_id,
                task=task,
                feature_hash=_PLACEHOLDER_HASH,
                started=started,
                privacy_ms=(perf_counter() - privacy_started) * 1000,
                resident=False,
                mock=self.mock,
            )
        privacy_ms = (perf_counter() - privacy_started) * 1000

        # Resolve the candidate against the current registry snapshot.
        routing_started = perf_counter()
        snapshot = self._registry.snapshot()
        entries = {entry.manifest.id: entry for entry in snapshot.roster.entries}
        entry = entries.get(model_id)
        if entry is None:
            return _early_failure(
                status=ObservationStatus.PROVIDER_FAILURE,
                error_code=ErrorCode.UNAVAILABLE,
                model_id=model_id,
                task=task,
                feature_hash=_PLACEHOLDER_HASH,
                started=started,
                privacy_ms=privacy_ms,
                routing_ms=(perf_counter() - routing_started) * 1000,
                resident=False,
                mock=self.mock,
            )
        model_version = entry.manifest.version
        resident_before_dispatch = bool(entry.resident)
        try:
            feature_row = extract_features(
                sanitized.payload,
                task,
                entry,
                hardware or snapshot.hardware,
                mock_mode=snapshot.mock_mode,
            )
        except Exception:
            return _early_failure(
                status=ObservationStatus.PROVIDER_FAILURE,
                error_code=ErrorCode.INTERNAL,
                model_id=model_id,
                model_version=model_version,
                task=task,
                feature_hash=_PLACEHOLDER_HASH,
                started=started,
                privacy_ms=privacy_ms,
                routing_ms=(perf_counter() - routing_started) * 1000,
                resident=resident_before_dispatch,
                mock=self.mock,
            )
        feature_hash = canonical_hash(
            feature_row.model_dump(mode="json", warnings=False),
            namespace=_HASH_NAMESPACE,
        )

        # Lease the one target; any lease failure is a provider_failure.
        try:
            lease = self._registry.lease((model_id,), snapshot=snapshot)
        except Exception:
            return _early_failure(
                status=ObservationStatus.PROVIDER_FAILURE,
                error_code=ErrorCode.UNAVAILABLE,
                model_id=model_id,
                model_version=model_version,
                task=task,
                feature_hash=feature_hash,
                started=started,
                privacy_ms=privacy_ms,
                routing_ms=(perf_counter() - routing_started) * 1000,
                resident=resident_before_dispatch,
                mock=self.mock,
                features=feature_row,
            )
        routing_ms = (perf_counter() - routing_started) * 1000

        context = RequestContext(
            request_id,
            "",
            sanitized_payload=sanitized.payload,
            privacy_placeholders=sanitized.placeholders,
        )
        session = self._dispatcher.new_request(
            sanitized.payload,
            request_id=request_id,
            mode=ExecutionMode.LOCAL_ONLY,
            options=options,
            task=task,
            context=context,
        )
        generation_started = perf_counter()
        try:
            outcome = await self._dispatcher.dispatch(session, model_id)
        finally:
            lease.release()
        generation_ms = (perf_counter() - generation_started) * 1000

        if isinstance(outcome, ProviderFailure):
            context.clear()
            return EvaluationResult(
                status=ObservationStatus.PROVIDER_FAILURE,
                model_id=outcome.model_id,
                model_version=outcome.model_version,
                task=task,
                feature_hash=feature_hash,
                features=feature_row,
                error_code=outcome.error_code,
                total_ms=(perf_counter() - started) * 1000,
                privacy_ms=privacy_ms,
                routing_ms=routing_ms,
                generation_ms=generation_ms,
                inspection_ms=0.0,
                input_tokens=None,
                output_tokens=None,
                resident=resident_before_dispatch,
                policy_version=POLICY_VERSION,
                mock=self.mock,
            )
        if not isinstance(outcome, ProviderSuccess):
            context.clear()
            return EvaluationResult(
                status=ObservationStatus.PROVIDER_FAILURE,
                model_id=model_id,
                model_version=model_version,
                task=task,
                feature_hash=feature_hash,
                features=feature_row,
                error_code=ErrorCode.INTERNAL,
                total_ms=(perf_counter() - started) * 1000,
                privacy_ms=privacy_ms,
                routing_ms=routing_ms,
                generation_ms=generation_ms,
                inspection_ms=0.0,
                resident=resident_before_dispatch,
                policy_version=POLICY_VERSION,
                mock=self.mock,
            )

        inspection_started = perf_counter()
        inspection = self._inspector.inspect(request_id, outcome.text, sanitized.placeholders)
        inspection_ms = (perf_counter() - inspection_started) * 1000
        context.clear()
        if inspection.warning:
            return EvaluationResult(
                status=ObservationStatus.INSPECTION_BLOCKED,
                model_id=outcome.model_id,
                model_version=outcome.model_version,
                task=task,
                feature_hash=feature_hash,
                features=feature_row,
                error_code=ErrorCode.OUTPUT_BLOCKED,
                total_ms=(perf_counter() - started) * 1000,
                privacy_ms=privacy_ms,
                routing_ms=routing_ms,
                generation_ms=generation_ms,
                inspection_ms=inspection_ms,
                load_ms=_load_ms(outcome),
                prompt_ms=_prompt_ms(outcome),
                input_tokens=_input_tokens(outcome),
                output_tokens=_output_tokens(outcome),
                resident=resident_before_dispatch,
                policy_version=POLICY_VERSION,
                mock=self.mock,
            )
        return EvaluationResult(
            status=ObservationStatus.SUCCESS,
            model_id=outcome.model_id,
            model_version=outcome.model_version,
            task=task,
            feature_hash=feature_hash,
            features=feature_row,
            answer=inspection.answer,
            total_ms=(perf_counter() - started) * 1000,
            privacy_ms=privacy_ms,
            routing_ms=routing_ms,
            generation_ms=generation_ms,
            inspection_ms=inspection_ms,
            load_ms=_load_ms(outcome),
            prompt_ms=_prompt_ms(outcome),
            input_tokens=_input_tokens(outcome),
            output_tokens=_output_tokens(outcome),
            resident=resident_before_dispatch,
            policy_version=POLICY_VERSION,
            mock=self.mock,
        )


_PLACEHOLDER_HASH = re.sub(r".", "0", "x" * 64)


def _load_ms(outcome: ProviderSuccess) -> float | None:
    if outcome.runtime_metrics is None:
        return None
    return outcome.runtime_metrics.load_ms


def _prompt_ms(outcome: ProviderSuccess) -> float | None:
    if outcome.runtime_metrics is None:
        return None
    return outcome.runtime_metrics.prompt_ms


def _input_tokens(outcome: ProviderSuccess) -> int | None:
    if outcome.token_metrics is None:
        return None
    return outcome.token_metrics.input_tokens


def _output_tokens(outcome: ProviderSuccess) -> int | None:
    if outcome.token_metrics is None:
        return None
    return outcome.token_metrics.output_tokens


def _early_failure(
    *,
    status: ObservationStatus,
    error_code: ErrorCode,
    model_id: str,
    task: TaskCategory,
    feature_hash: str,
    started: float,
    privacy_ms: float,
    routing_ms: float = 0.0,
    resident: bool,
    mock: bool,
    model_version: str = "unavailable",
    features: FeatureRow | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        status=status,
        model_id=model_id,
        model_version=model_version,
        task=task,
        feature_hash=feature_hash,
        features=features,
        error_code=error_code,
        total_ms=(perf_counter() - started) * 1000,
        privacy_ms=privacy_ms,
        routing_ms=routing_ms,
        generation_ms=0.0,
        inspection_ms=0.0,
        resident=resident,
        policy_version=POLICY_VERSION,
        mock=mock,
    )
