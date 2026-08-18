from __future__ import annotations

from agentlab.budget import BudgetTracker
from agentlab.schema import Budget, PerTrialBudget


def test_no_wall_clock_means_no_deadline() -> None:
    tracker = BudgetTracker(Budget())
    assert tracker.trial_deadline() is None
    assert tracker.exceeded() is False


def test_per_trial_cap_still_sets_deadline() -> None:
    tracker = BudgetTracker(Budget(per_trial=PerTrialBudget(wall_clock_s=10)))
    deadline = tracker.trial_deadline()
    assert deadline is not None
    assert 0 < deadline - tracker.started <= 10.1
