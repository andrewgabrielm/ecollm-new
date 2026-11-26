"""One request's bounded compute time, separated from a single human approval pause.

The caller serializes access and owns timers/request cleanup. This helper never
sleeps, dispatches generation or refreshes a consumed compute budget.
"""

import math
from collections.abc import Callable
from time import monotonic

from pydantic import ValidationError

from backend.config.loader import ComputeBudgets


class DeadlineError(RuntimeError):
    """Safe deadline failure without caller data or clock exception details."""


class ComputeBudget:
    __slots__ = (
        "_approval_expires_at",
        "_budgets",
        "_clock",
        "_closed",
        "_deadline",
        "_last_now",
        "_pause_used",
        "_paused",
        "_saved_remaining",
    )

    def __init__(
        self,
        budgets: ComputeBudgets | None = None,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._closed = False
        self._paused = False
        self._pause_used = False
        self._approval_expires_at: float | None = None
        self._saved_remaining = 0.0
        self._deadline = 0.0
        self._last_now: float | None = None
        self._clock = clock
        try:
            if budgets is not None and not isinstance(budgets, ComputeBudgets):
                raise DeadlineError("Invalid compute budget configuration")
            # Pydantic copy/update deliberately bypasses validation; never trust
            # caller-owned configuration merely because its class is correct.
            self._budgets = ComputeBudgets.model_validate(
                ComputeBudgets().model_dump() if budgets is None else budgets.model_dump()
            )
        except (ValidationError, TypeError, ValueError, AttributeError):
            raise DeadlineError("Invalid compute budget configuration") from None
        self._deadline = self._deadline_after(self._now(), self._budgets.overall_seconds)

    def _now(self) -> float:
        try:
            current = self._clock()
        except Exception:
            self.close()
            raise DeadlineError("Compute clock is unavailable") from None
        if (
            isinstance(current, bool)
            or not isinstance(current, (int, float))
            or not math.isfinite(current)
            or current < 0
            or (self._last_now is not None and current < self._last_now)
        ):
            self.close()
            raise DeadlineError("Compute clock is invalid")
        self._last_now = float(current)
        return self._last_now

    def _deadline_after(self, now: float, seconds: float) -> float:
        deadline = now + seconds
        if not math.isfinite(deadline) or deadline <= now:
            self.close()
            raise DeadlineError("Compute deadline is unavailable")
        return deadline

    @property
    def remaining_seconds(self) -> float:
        if self._closed:
            return 0.0
        now = self._now()
        if self._paused:
            if self._approval_expires_at is None or now >= self._approval_expires_at:
                return 0.0
            return self._saved_remaining
        return max(0.0, self._deadline - now)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def approval_expires_at(self) -> float | None:
        return self._approval_expires_at

    def pause(self, expires_at: float) -> None:
        if self._closed:
            raise DeadlineError("Compute budget is closed")
        now = self._now()
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
            or expires_at <= now
        ):
            raise DeadlineError("Approval window is unavailable")
        if self._paused:
            if expires_at != self._approval_expires_at:
                raise DeadlineError("Approval expiry cannot be refreshed")
            return  # Same proposal: preserve its original deadline and saved compute.
        if self._pause_used:
            raise DeadlineError("Compute budget cannot be paused again")
        if expires_at > now + self._budgets.approval_seconds:
            raise DeadlineError("Approval window exceeds its configured bound")
        remaining = max(0.0, self._deadline - now)
        if remaining <= 0:
            raise DeadlineError("Compute budget expired")
        self._saved_remaining = remaining
        self._approval_expires_at = float(expires_at)
        self._paused = True
        self._pause_used = True

    def resume(self) -> None:
        if self._closed:
            raise DeadlineError("Compute budget is closed")
        now = self._now()
        if not self._paused or self._approval_expires_at is None:
            raise DeadlineError("Compute budget is not paused")
        if now >= self._approval_expires_at:
            raise DeadlineError("Approval window expired")
        remaining = min(self._saved_remaining, self._budgets.resume_seconds)
        if remaining <= 0:
            raise DeadlineError("Compute budget expired")
        self._deadline = self._deadline_after(now, remaining)
        self._approval_expires_at = None
        self._saved_remaining = 0.0
        self._paused = False

    def close(self) -> None:
        self._closed = True
        self._paused = False
        self._approval_expires_at = None
        self._saved_remaining = 0.0
        self._deadline = 0.0
