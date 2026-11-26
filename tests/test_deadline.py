"""Monotonic fake-clock checks: no waits, runtimes or generation calls."""

from typing import Any, cast

import pytest

from backend.broker.deadline import ComputeBudget, DeadlineError
from backend.config.loader import ComputeBudgets


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_compute_time_counts_all_elapsed_time_without_implicit_reset() -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    assert budget.remaining_seconds == 120
    clock.now += 59
    assert budget.remaining_seconds == 61
    clock.now += 61
    assert budget.remaining_seconds == 0
    clock.now += 1
    assert budget.remaining_seconds == 0
    with pytest.raises(DeadlineError, match="expired"):
        budget.pause(clock.now + 10)


def test_human_approval_pause_does_not_spend_or_replenish_compute() -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    clock.now += 45
    expires = clock.now + 120
    budget.pause(expires)
    assert budget.paused
    assert budget.approval_expires_at == expires
    assert budget.remaining_seconds == 75
    clock.now += 110
    assert budget.remaining_seconds == 75
    budget.resume()
    assert not budget.paused
    assert budget.approval_expires_at is None
    assert budget.remaining_seconds == 75
    clock.now += 74
    assert budget.remaining_seconds == 1
    clock.now += 1
    assert budget.remaining_seconds == 0


def test_resume_cap_can_only_reduce_saved_compute() -> None:
    clock = FakeClock()
    budget = ComputeBudget(ComputeBudgets(resume_seconds=30), clock=clock)
    clock.now += 10
    budget.pause(clock.now + 120)
    assert budget.remaining_seconds == 110
    clock.now += 100
    budget.resume()
    assert budget.remaining_seconds == 30
    clock.now += 30
    assert budget.remaining_seconds == 0


def test_identical_active_proposal_does_not_refresh_pause_or_saved_budget() -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    clock.now += 10
    expires = clock.now + 120
    budget.pause(expires)
    clock.now += 100
    budget.pause(expires)
    assert budget.approval_expires_at == expires
    assert budget.remaining_seconds == 110
    with pytest.raises(DeadlineError, match="cannot be refreshed"):
        budget.pause(expires + 100)
    clock.now = expires
    assert budget.remaining_seconds == 0
    with pytest.raises(DeadlineError):
        budget.pause(expires)
    with pytest.raises(DeadlineError, match="expired"):
        budget.resume()


def test_request_cannot_gain_a_second_approval_pause_or_resume() -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    with pytest.raises(DeadlineError, match="not paused"):
        budget.resume()
    budget.pause(clock.now + 120)
    budget.resume()
    with pytest.raises(DeadlineError, match="not paused"):
        budget.resume()
    with pytest.raises(DeadlineError, match="cannot be paused again"):
        budget.pause(clock.now + 120)
    assert budget.remaining_seconds == 120


@pytest.mark.parametrize("elapsed", [120, 121, 1000])
def test_approval_expiry_cannot_resume_even_when_saved_compute_remains(elapsed: float) -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    budget.pause(clock.now + 120)
    clock.now += elapsed
    assert budget.remaining_seconds == 0
    with pytest.raises(DeadlineError, match="expired"):
        budget.resume()


@pytest.mark.parametrize("expiry", [100, 99, 0, -1, 221, float("nan"), float("inf"), True])
def test_invalid_or_unbounded_approval_window_rejected(expiry: float) -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    with pytest.raises(DeadlineError):
        budget.pause(expiry)
    assert not budget.paused
    assert budget.remaining_seconds == 120


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True, "not-a-clock"])
def test_invalid_clock_values_fail_closed(value: object) -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    clock.now = cast(Any, value)
    with pytest.raises(DeadlineError, match="clock is invalid"):
        _ = budget.remaining_seconds
    assert budget.closed
    assert budget.remaining_seconds == 0


def test_clock_regression_cannot_extend_compute_or_human_approval() -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    clock.now += 20
    assert budget.remaining_seconds == 100
    clock.now -= 1
    with pytest.raises(DeadlineError, match="clock is invalid"):
        _ = budget.remaining_seconds
    assert budget.closed


def test_external_clock_exception_never_echoes_details() -> None:
    def unavailable() -> float:
        raise RuntimeError("invented-private-clock-marker")

    with pytest.raises(DeadlineError) as caught:
        ComputeBudget(clock=unavailable)
    assert "invented-private" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_explicit_close_is_idempotent_and_disables_all_further_budget_use() -> None:
    clock = FakeClock()
    budget = ComputeBudget(clock=clock)
    budget.pause(clock.now + 120)
    budget.close()
    budget.close()
    assert budget.closed
    assert not budget.paused
    assert budget.approval_expires_at is None
    assert budget.remaining_seconds == 0
    with pytest.raises(DeadlineError, match="closed"):
        budget.pause(clock.now + 120)
    with pytest.raises(DeadlineError, match="closed"):
        budget.resume()


def test_config_copy_update_is_revalidated() -> None:
    for change in (
        {"overall_seconds": float("nan")},
        {"overall_seconds": 0},
        {"overall_seconds": 121},
        {"resume_seconds": True},
        {"resume_seconds": 121},
    ):
        with pytest.raises(DeadlineError, match="configuration"):
            ComputeBudget(ComputeBudgets().model_copy(update=change), clock=FakeClock())


def test_unrepresentable_deadline_is_rejected() -> None:
    with pytest.raises(DeadlineError, match="deadline is unavailable"):
        ComputeBudget(clock=FakeClock(1e308))
