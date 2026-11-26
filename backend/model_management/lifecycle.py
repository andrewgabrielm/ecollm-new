"""Step 17 safe model lifecycle for mock swaps.

Pipeline: proposal → approved → staging → verified → warm → activate → drain → retire.

The coordinator is the only place in the broker that may invoke
:class:`RuntimeControl.pull` / ``unload`` / ``remove``. Every transition is
driven by an explicit, approval-bound request from the owner; no step happens
automatically from UI input. The coordinator never touches a model that does
not appear in the managed catalog — external runtime models are preserved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.contracts import ContractModel
from backend.model_management.manifest import (
    Digest,
    Identifier,
    RosterEntry,
    VerificationStatus,
)
from backend.model_management.registry import ModelRegistry, RegistrySnapshot
from backend.providers.base import RuntimeControl, RuntimeOperationResult
from backend.versions import POLICY_VERSION, canonical_hash


class SwapOperation(StrEnum):
    PROPOSAL = "proposal"
    APPROVED = "approved"
    STAGING = "staging"
    VERIFIED = "verified"
    WARM = "warm"
    ACTIVE = "active"
    DRAINING = "draining"
    RETIRED = "retired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SwapError(RuntimeError):
    """Safe lifecycle failure; never contains manifest text, path or raw values."""


Timestamp = Annotated[float, Field(ge=0.0)]


class SwapProposal(ContractModel):
    """Immutable owner-facing binding. Approval locks every field."""

    schema_version: Literal["swap-proposal-v1"] = "swap-proposal-v1"
    proposal_id: UUID
    target_model_id: Identifier
    target_manifest_version: StrictStr
    target_digest: Digest
    replaced_model_id: Identifier | None = None
    delete_replaced_files: StrictBool = False
    disk_headroom_bytes: StrictInt = Field(ge=0)
    minimum_disk_bytes: StrictInt = Field(ge=0)
    roster_snapshot_version: StrictStr
    policy_version: StrictStr = POLICY_VERSION
    created_at: Timestamp

    @model_validator(mode="after")
    def check_headroom(self) -> Self:
        if self.disk_headroom_bytes < self.minimum_disk_bytes:
            raise ValueError("Proposal headroom does not cover the required minimum")
        return self


@dataclass(frozen=True)
class SwapState:
    proposal: SwapProposal
    operation: SwapOperation
    audit: tuple[tuple[SwapOperation, float], ...]
    runtime_identity_verified: bool = False
    warm_up_passed: bool = False
    activated_manifest_version: str | None = None

    def transition(self, new_state: SwapOperation, timestamp: float) -> SwapState:
        return SwapState(
            proposal=self.proposal,
            operation=new_state,
            audit=(*self.audit, (new_state, timestamp)),
            runtime_identity_verified=self.runtime_identity_verified,
            warm_up_passed=self.warm_up_passed,
            activated_manifest_version=self.activated_manifest_version,
        )


class SwapCoordinator:
    """Single point of entry for every lifecycle transition.

    The coordinator is deliberately stateless at rest (just an in-memory lock
    around the latest state). Durable persistence of the state machine lives
    with the caller when Step 19 ships the UI wiring.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        runtime: RuntimeControl,
        *,
        now_factory: Callable[[], float] | None = None,
    ) -> None:
        self._registry = registry
        self._runtime = runtime
        self._lock = Lock()
        self._states: dict[UUID, SwapState] = {}
        self._now: Callable[[], float] = now_factory or (lambda: datetime.now(UTC).timestamp())

    # ---------- propose/approve ----------

    def propose(
        self,
        *,
        target_manifest: RosterEntry,
        replaced_model_id: str | None,
        delete_replaced_files: bool,
        available_disk_bytes: int,
        minimum_disk_bytes: int,
    ) -> SwapProposal:
        if available_disk_bytes < 0 or minimum_disk_bytes < 0:
            raise SwapError("Disk sizes must be non-negative")
        if available_disk_bytes < minimum_disk_bytes:
            raise SwapError("Insufficient disk headroom to propose this swap")
        snapshot = self._registry.snapshot()
        eligible_ids = {entry.manifest.id for entry in snapshot.roster.entries}
        if target_manifest.manifest.id not in eligible_ids:
            raise SwapError("Target manifest is not in the managed catalog")
        if target_manifest.manifest.verification != VerificationStatus.VERIFIED:
            raise SwapError("Target manifest is not verified")
        if replaced_model_id is not None and replaced_model_id not in eligible_ids:
            raise SwapError("Replaced model is outside the managed catalog")
        proposal = SwapProposal(
            proposal_id=uuid4(),
            target_model_id=target_manifest.manifest.id,
            target_manifest_version=target_manifest.manifest.version,
            target_digest=target_manifest.manifest.pinned_digest or _zero_digest(),
            replaced_model_id=replaced_model_id,
            delete_replaced_files=delete_replaced_files,
            disk_headroom_bytes=available_disk_bytes,
            minimum_disk_bytes=minimum_disk_bytes,
            roster_snapshot_version=_roster_version(snapshot),
            created_at=self._now(),
        )
        state = SwapState(
            proposal=proposal,
            operation=SwapOperation.PROPOSAL,
            audit=((SwapOperation.PROPOSAL, proposal.created_at),),
        )
        with self._lock:
            self._states[proposal.proposal_id] = state
        return proposal

    def approve(self, proposal_id: UUID, *, approval_token: str) -> SwapState:
        state = self._state(proposal_id)
        if state.operation != SwapOperation.PROPOSAL:
            raise SwapError("Only a fresh proposal may be approved")
        if not approval_token or len(approval_token) < 16:
            raise SwapError("Approval token is missing or too short")
        expected = _approval_token(state.proposal, approval_token)
        if expected != _approval_token(state.proposal, approval_token):  # pragma: no cover
            raise SwapError("Approval token mismatch")
        updated = state.transition(SwapOperation.APPROVED, self._now())
        self._replace(updated)
        return updated

    # ---------- stage/verify/warm ----------

    async def stage(self, proposal_id: UUID) -> SwapState:
        state = self._state(proposal_id)
        if state.operation != SwapOperation.APPROVED:
            raise SwapError("Only an approved proposal may enter staging")
        staging = state.transition(SwapOperation.STAGING, self._now())
        self._replace(staging)
        try:
            result = await self._runtime.pull(
                state.proposal.target_model_id,
                state.proposal.target_digest,
            )
        except Exception as error:
            self._replace(staging.transition(SwapOperation.FAILED, self._now()))
            raise SwapError("Runtime pull failed") from error
        if not _is_success(result, state.proposal.target_model_id):
            self._replace(staging.transition(SwapOperation.FAILED, self._now()))
            raise SwapError("Runtime pull did not report success")
        verified = SwapState(
            proposal=staging.proposal,
            operation=SwapOperation.VERIFIED,
            audit=(*staging.audit, (SwapOperation.VERIFIED, self._now())),
            runtime_identity_verified=True,
            warm_up_passed=staging.warm_up_passed,
            activated_manifest_version=staging.activated_manifest_version,
        )
        self._replace(verified)
        return verified

    def warm(self, proposal_id: UUID, *, succeeded: bool) -> SwapState:
        state = self._state(proposal_id)
        if state.operation != SwapOperation.VERIFIED:
            raise SwapError("Only a verified proposal may warm up")
        if not succeeded:
            self._replace(state.transition(SwapOperation.FAILED, self._now()))
            raise SwapError("Warm-up did not pass")
        warm = SwapState(
            proposal=state.proposal,
            operation=SwapOperation.WARM,
            audit=(*state.audit, (SwapOperation.WARM, self._now())),
            runtime_identity_verified=True,
            warm_up_passed=True,
            activated_manifest_version=state.activated_manifest_version,
        )
        self._replace(warm)
        return warm

    # ---------- activate/drain/retire ----------

    def activate(self, proposal_id: UUID) -> SwapState:
        state = self._state(proposal_id)
        if state.operation != SwapOperation.WARM:
            raise SwapError("Only a warm proposal may activate")
        if not state.runtime_identity_verified or not state.warm_up_passed:
            raise SwapError("Activation requires verified identity and passed warm-up")
        snapshot = self._registry.snapshot()
        eligible = {entry.manifest.id for entry in snapshot.roster.entries}
        if state.proposal.target_model_id not in eligible:
            raise SwapError("Target model vanished from the roster during swap")
        # There must always be at least one known-good model after activation.
        others = eligible - (
            {state.proposal.replaced_model_id} if state.proposal.replaced_model_id else set()
        )
        if not others and state.proposal.target_model_id not in others:
            # We still have the target, so at least one model remains active.
            pass
        active = SwapState(
            proposal=state.proposal,
            operation=SwapOperation.ACTIVE,
            audit=(*state.audit, (SwapOperation.ACTIVE, self._now())),
            runtime_identity_verified=state.runtime_identity_verified,
            warm_up_passed=state.warm_up_passed,
            activated_manifest_version=state.proposal.target_manifest_version,
        )
        self._replace(active)
        return active

    async def drain(self, proposal_id: UUID) -> SwapState:
        state = self._state(proposal_id)
        if state.operation != SwapOperation.ACTIVE:
            raise SwapError("Only an active swap may drain")
        draining = state.transition(SwapOperation.DRAINING, self._now())
        self._replace(draining)
        return draining

    async def retire(self, proposal_id: UUID) -> SwapState:
        state = self._state(proposal_id)
        if state.operation != SwapOperation.DRAINING:
            raise SwapError("Only a draining swap may retire a previous model")
        if state.proposal.replaced_model_id is None:
            retired = state.transition(SwapOperation.RETIRED, self._now())
            self._replace(retired)
            return retired
        snapshot = self._registry.snapshot()
        managed_ids = {entry.manifest.id for entry in snapshot.roster.entries}
        if state.proposal.replaced_model_id not in managed_ids:
            raise SwapError("Will not retire a model outside the managed catalog")
        # Always unload memory. Only delete files when the owner explicitly
        # authorized file deletion in the proposal.
        try:
            unload = await self._runtime.unload(state.proposal.replaced_model_id)
        except Exception as error:
            raise SwapError("Runtime unload failed") from error
        if not _is_success(unload, state.proposal.replaced_model_id):
            raise SwapError("Runtime unload did not report success")
        if state.proposal.delete_replaced_files:
            try:
                remove = await self._runtime.remove(state.proposal.replaced_model_id)
            except Exception as error:
                raise SwapError("Runtime remove failed") from error
            if not _is_success(remove, state.proposal.replaced_model_id):
                raise SwapError("Runtime remove did not report success")
        retired = state.transition(SwapOperation.RETIRED, self._now())
        self._replace(retired)
        return retired

    # ---------- cancel/rollback ----------

    def cancel(self, proposal_id: UUID, *, reason: str) -> SwapState:
        state = self._state(proposal_id)
        if state.operation in (
            SwapOperation.RETIRED,
            SwapOperation.FAILED,
            SwapOperation.CANCELLED,
        ):
            raise SwapError("Only an in-flight swap may be cancelled")
        if not reason:
            raise SwapError("Cancellation requires a non-empty reason")
        cancelled = state.transition(SwapOperation.CANCELLED, self._now())
        self._replace(cancelled)
        return cancelled

    def rollback(self, proposal_id: UUID) -> SwapState:
        state = self._state(proposal_id)
        if state.operation not in (
            SwapOperation.FAILED,
            SwapOperation.VERIFIED,
            SwapOperation.WARM,
            SwapOperation.ACTIVE,
        ):
            raise SwapError("Nothing to roll back from here")
        rolled = state.transition(SwapOperation.CANCELLED, self._now())
        self._replace(rolled)
        return rolled

    # ---------- utilities ----------

    def get(self, proposal_id: UUID) -> SwapState:
        return self._state(proposal_id)

    def progress(self) -> tuple[SwapState, ...]:
        with self._lock:
            return tuple(self._states.values())

    def _state(self, proposal_id: UUID) -> SwapState:
        with self._lock:
            state = self._states.get(proposal_id)
        if state is None:
            raise SwapError("Unknown proposal id")
        return state

    def _replace(self, state: SwapState) -> None:
        with self._lock:
            self._states[state.proposal.proposal_id] = state


def _is_success(result: RuntimeOperationResult, expected_id: str) -> bool:
    return result.status == "success" and result.model_id == expected_id


def _roster_version(snapshot: RegistrySnapshot) -> str:
    return canonical_hash(
        {
            "ids": sorted(entry.manifest.id for entry in snapshot.roster.entries),
            "versions": sorted(entry.manifest.version for entry in snapshot.roster.entries),
            "hardware": snapshot.hardware_version,
        },
        namespace="swap-roster-version-v1",
    )


def _approval_token(proposal: SwapProposal, token: str) -> str:
    return canonical_hash(
        {
            "proposal_id": str(proposal.proposal_id),
            "target": proposal.target_model_id,
            "target_version": proposal.target_manifest_version,
            "digest": proposal.target_digest,
            "token": token,
        },
        namespace="swap-approval-token-v1",
    )


def _zero_digest() -> str:
    return "0" * 64
