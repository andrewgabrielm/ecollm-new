"""Fail-closed generation authorization and bounded, one-use in-memory approvals.

Only the orchestrator calls this module immediately before generation. A proposal
is a display value, not authorization; its receipt is internal and nonserializable.
This is a code boundary, not an operating-system or hostile-process sandbox.
"""

import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Annotated, NoReturn, SupportsIndex
from uuid import UUID, uuid4

from pydantic import Field, StrictStr

from backend.api.schemas import CloudFallbackReason, GenerationOptions, Identifier, Version
from backend.config.endpoints import validate_endpoint
from backend.config.loader import PolicyConfig
from backend.contracts import ContractModel, ErrorCode, ExecutionMode, TaskCategory
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.providers.base import ProviderDescriptor
from backend.versions import canonical_hash

Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]


class PolicyViolation(RuntimeError):
    """Content-free policy outcome; never preserve upstream diagnostic strings."""

    def __init__(self, code: ErrorCode = ErrorCode.POLICY_BLOCKED) -> None:
        self.code = code
        super().__init__(f"Generation authorization failed: {code.value}")


class ApprovalNeeded(PolicyViolation):
    """All cloud eligibility checks passed, but explicit approval is required."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.APPROVAL_CONFLICT)


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    request_id: UUID
    payload: SanitizedPayload | None = field(repr=False)
    mode: ExecutionMode
    options: GenerationOptions
    task: TaskCategory | None
    remaining_seconds: float


class CloudProposal(ContractModel):
    proposal_id: UUID
    request_id: UUID
    digest: Digest
    target_fingerprint: Digest
    model_id: Identifier
    model_version: Version
    endpoint: StrictStr
    mode: ExecutionMode
    policy_version: Digest
    options_digest: Digest
    reason: CloudFallbackReason
    sanitized_preview: StrictStr = Field(min_length=1, max_length=64000, repr=False)
    expires_at: float = Field(gt=0, strict=True)


class ApprovalReceipt:
    """A server-memory capability, never a frontend token or persisted record."""

    __slots__ = ("_proposal_id", "_token")

    def __init__(self, proposal_id: UUID, token: str) -> None:
        self._proposal_id = proposal_id
        self._token = token

    def __repr__(self) -> str:
        return "ApprovalReceipt(<redacted>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("approval receipt serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("approval receipt serialization is forbidden")

    def model_dump(self) -> NoReturn:
        raise TypeError("approval receipt serialization is forbidden")

    def model_dump_json(self) -> NoReturn:
        raise TypeError("approval receipt serialization is forbidden")


class _ApprovalRecord:
    __slots__ = ("proposal", "token")

    def __init__(self, proposal: CloudProposal) -> None:
        self.proposal = proposal
        self.token: str | None = None

    def __repr__(self) -> str:
        return "ApprovalRecord(<redacted>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("approval record serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("approval record serialization is forbidden")


class PolicyGate:
    """Evaluate current policy and consume receipts in one synchronous critical section.

    The dispatcher schedules idle expiry/cleanup and owns request compute budgets.
    The gate additionally purges expired records on every public operation. No raw
    prompt, placeholder map, provider or credential is retained here.
    """

    def __init__(
        self,
        policy: PolicyConfig | None = None,
        *,
        approval_seconds: float = 120,
        max_pending: int = 128,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            isinstance(approval_seconds, bool)
            or not isinstance(approval_seconds, (float, int))
            or not math.isfinite(approval_seconds)
            or not 0 < approval_seconds <= 120
            or isinstance(max_pending, bool)
            or not isinstance(max_pending, int)
            or not 1 <= max_pending <= 4096
        ):
            raise ValueError("Invalid approval store bounds")
        self._policy = self._validated_policy(policy or PolicyConfig())
        self._approval_seconds = float(approval_seconds)
        self._max_pending = max_pending
        self._clock = clock
        self._clock_lock = Lock()
        self._last_now: float | None = None
        self._lock = Lock()
        self._records: dict[UUID, _ApprovalRecord] = {}

    def __repr__(self) -> str:
        return "PolicyGate(<redacted>)"

    @staticmethod
    def _validated_policy(policy: PolicyConfig) -> PolicyConfig:
        try:
            return PolicyConfig.model_validate(policy.model_dump())
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Invalid policy configuration") from None

    def now(self) -> float:
        with self._clock_lock:
            try:
                value = self._clock()
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                    or (self._last_now is not None and value < self._last_now)
                ):
                    raise PolicyViolation(ErrorCode.INTERNAL)
                self._last_now = float(value)
                return float(value)
            except Exception:
                raise PolicyViolation(ErrorCode.INTERNAL) from None

    @property
    def policy_version(self) -> str:
        with self._lock:
            self._purge_locked(self.now())
            return self._policy.version

    @property
    def policy(self) -> PolicyConfig:
        """Read-only current rules; reading them never grants dispatch permission."""
        with self._lock:
            return self._policy

    @property
    def pending_count(self) -> int:
        with self._lock:
            self._purge_locked(self.now())
            return len(self._records)

    def set_policy(self, policy: PolicyConfig) -> None:
        validated = self._validated_policy(policy)
        with self._lock:
            self._purge_locked(self.now())
            if validated.version != self._policy.version:
                self._records.clear()
            self._policy = validated

    def cancel(self, request_id: UUID) -> None:
        with self._lock:
            self._records = {
                key: record
                for key, record in self._records.items()
                if record.proposal.request_id != request_id
            }
            try:
                self._purge_locked(self.now())
            except PolicyViolation:
                # Cleanup must not depend on a clock which may itself be failing.
                self._records.clear()

    def purge_expired(self) -> None:
        with self._lock:
            self._purge_locked(self.now())

    def _purge_locked(self, now: float) -> None:
        self._records = {
            key: record for key, record in self._records.items() if now < record.proposal.expires_at
        }

    def _check_locked(
        self, check: PolicyCheck, target: ProviderDescriptor, reason: CloudFallbackReason | None
    ) -> tuple[SanitizedPayload, ProviderDescriptor, str]:
        """Validate everything before revealing that approval could be requested."""
        try:
            if (
                not isinstance(check, PolicyCheck)
                or not isinstance(check.request_id, UUID)
                or not isinstance(check.mode, ExecutionMode)
                or (check.task is not None and not isinstance(check.task, TaskCategory))
                or isinstance(check.remaining_seconds, bool)
                or not isinstance(check.remaining_seconds, (float, int))
                or not math.isfinite(check.remaining_seconds)
                or check.remaining_seconds > 120
            ):
                raise PolicyViolation(ErrorCode.INVALID_REQUEST)
            if check.remaining_seconds <= 0:
                raise PolicyViolation(ErrorCode.TIMEOUT)
            payload = check.payload
            if (
                not isinstance(payload, SanitizedPayload)
                or not payload.is_trusted
                or payload.request_id != check.request_id
            ):
                raise PolicyViolation()
            # Revalidation catches model_copy/model_construct schema bypasses.
            SanitizedPayload.model_validate(payload.model_dump())
            options = GenerationOptions.model_validate(check.options.model_dump())
            validated_target = ProviderDescriptor.model_validate(target.model_dump())
            validate_endpoint(validated_target.endpoint, local=validated_target.route == "local")
            options_digest = canonical_hash(options.model_dump(mode="json"), namespace="options-v1")
            if validated_target.route == "local":
                return payload, validated_target, options_digest
            if check.mode == ExecutionMode.LOCAL_ONLY:
                raise PolicyViolation()
            if any(
                entry.entity_type in self._policy.hard_block_entity_types
                for entry in payload.privacy_counts
            ):
                raise PolicyViolation()
            if not isinstance(reason, CloudFallbackReason):
                raise PolicyViolation()
            # allowed_fallback_reasons governs falling back from a local attempt.
            # Cloud-only made no local attempt, so it is authorized by the mode
            # itself. The pairing is exact in both directions: the reason is
            # rejected outside that mode, and no other reason is accepted inside
            # it, so a fallback reason cannot smuggle a request past the list.
            if (reason == CloudFallbackReason.CLOUD_ONLY) != (
                check.mode == ExecutionMode.CLOUD_ONLY
            ):
                raise PolicyViolation()
            if (
                reason != CloudFallbackReason.CLOUD_ONLY
                and reason not in self._policy.allowed_fallback_reasons
            ):
                raise PolicyViolation()
            if (
                reason == CloudFallbackReason.NO_LOCAL_MODEL
                and not self._policy.allow_no_local_model_cloud
            ):
                raise PolicyViolation()
            if (
                reason == CloudFallbackReason.CLOUD_PREFERRED
                and check.task not in self._policy.cloud_preferred_categories
            ):
                raise PolicyViolation()
            if not validated_target.key_available:
                raise PolicyViolation()
            return payload, validated_target, options_digest
        except PolicyViolation:
            raise
        except (AttributeError, TypeError, ValueError):
            raise PolicyViolation() from None

    def propose(
        self, check: PolicyCheck, target: ProviderDescriptor, reason: CloudFallbackReason
    ) -> CloudProposal:
        with self._lock:
            now = self.now()
            self._purge_locked(now)
            payload, validated_target, options_digest = self._check_locked(check, target, reason)
            if validated_target.route != "cloud" or check.mode != ExecutionMode.ASK_BEFORE_CLOUD:
                raise PolicyViolation()
            for record in self._records.values():
                if record.proposal.request_id == check.request_id:
                    if self._matches(
                        record.proposal, check, validated_target, reason, options_digest
                    ):
                        return record.proposal
                    raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
            if len(self._records) >= self._max_pending:
                raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
            proposal = CloudProposal(
                proposal_id=uuid4(),
                request_id=check.request_id,
                digest=payload.digest,
                target_fingerprint=validated_target.fingerprint,
                model_id=validated_target.model_id,
                model_version=validated_target.model_version,
                endpoint=validated_target.endpoint,
                mode=check.mode,
                policy_version=self._policy.version,
                options_digest=options_digest,
                reason=reason,
                sanitized_preview=payload.text,
                expires_at=now + self._approval_seconds,
            )
            self._records[proposal.proposal_id] = _ApprovalRecord(proposal)
            return proposal

    def approve(self, proposal: CloudProposal) -> ApprovalReceipt:
        with self._lock:
            self._purge_locked(self.now())
            try:
                validated = CloudProposal.model_validate(proposal.model_dump())
                record = self._records.get(validated.proposal_id)
                if record is None or record.token is not None or validated != record.proposal:
                    raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
                if validated.policy_version != self._policy.version:
                    raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
                token = secrets.token_urlsafe(32)
                record.token = token
                return ApprovalReceipt(validated.proposal_id, token)
            except PolicyViolation:
                raise
            except (AttributeError, TypeError, ValueError):
                raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT) from None

    def _matches(
        self,
        proposal: CloudProposal,
        check: PolicyCheck,
        target: ProviderDescriptor,
        reason: CloudFallbackReason | None,
        options_digest: str,
    ) -> bool:
        payload = check.payload
        return (
            payload is not None
            and proposal.request_id == check.request_id
            and proposal.digest == payload.digest
            and proposal.sanitized_preview == payload.text
            and proposal.target_fingerprint == target.fingerprint
            and proposal.model_id == target.model_id
            and proposal.model_version == target.model_version
            and proposal.endpoint == target.endpoint
            and proposal.mode == check.mode
            and proposal.policy_version == self._policy.version
            and proposal.options_digest == options_digest
            and proposal.reason == reason
        )

    def authorize(
        self,
        check: PolicyCheck,
        target: ProviderDescriptor,
        reason: CloudFallbackReason | None = None,
        approval: ApprovalReceipt | None = None,
    ) -> None:
        with self._lock:
            now = self.now()
            self._purge_locked(now)
            _, validated_target, options_digest = self._check_locked(check, target, reason)
            if approval is None:
                if (
                    validated_target.route == "cloud"
                    and check.mode == ExecutionMode.ASK_BEFORE_CLOUD
                ):
                    raise ApprovalNeeded()
                return
            # Supplying a receipt never downgrades to local or automatic cloud authorization.
            if not isinstance(approval, ApprovalReceipt):
                raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
            try:
                if not isinstance(approval._proposal_id, UUID) or not isinstance(
                    approval._token, str
                ):
                    raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
                record = self._records.get(approval._proposal_id)
                if (
                    record is None
                    or record.token is None
                    or not secrets.compare_digest(record.token, approval._token)
                    or validated_target.route != "cloud"
                    or not self._matches(
                        record.proposal, check, validated_target, reason, options_digest
                    )
                ):
                    raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
                # Removal happens under the same lock as all current-policy checks.
                del self._records[approval._proposal_id]
            except (AttributeError, TypeError, ValueError):
                raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT) from None
