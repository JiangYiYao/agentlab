from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from agentlab.errors import BudgetExceeded
from agentlab.models import Trial
from agentlab.schema import Budget


class BudgetTracker:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.started = time.time()
        self.used_usd = 0.0
        self.used_tokens = 0
        self.running = 0
        self.exceeded_reason: str | None = None

    def exceeded(self) -> bool:
        if self.budget.wall_clock_s is not None and time.time() - self.started > self.budget.wall_clock_s:
            self.exceeded_reason = "budget_experiment"
            return True
        reserved_usd = self.running * (self.budget.per_trial.usd or 0)
        reserved_tokens = self.running * (self.budget.per_trial.tokens or 0)
        if self.budget.usd is not None and self.used_usd + reserved_usd > self.budget.usd:
            self.exceeded_reason = "budget_usd"
            return True
        if self.budget.tokens is not None and self.used_tokens + reserved_tokens > self.budget.tokens:
            self.exceeded_reason = "budget_tokens"
            return True
        return False

    def trial_deadline(self) -> float | None:
        now = time.time()
        remaining: list[float] = []
        if self.budget.wall_clock_s is not None:
            remaining.append(self.started + self.budget.wall_clock_s - now)
        if self.budget.per_trial.wall_clock_s is not None:
            remaining.append(float(self.budget.per_trial.wall_clock_s))
        if not remaining:
            return None
        return now + max(0.1, min(remaining))

    @contextmanager
    def trial_watch(self, trial: Trial) -> Iterator[None]:
        self.running += 1
        deadline = self.trial_deadline()
        try:
            yield
            if deadline is not None and time.time() > deadline:
                raise BudgetExceeded("timeout")
        finally:
            self.running -= 1
            if trial.result and trial.result.usage.usd:
                self.used_usd += trial.result.usage.usd
            if trial.result and trial.result.usage.tokens_in is not None:
                self.used_tokens += (trial.result.usage.tokens_in or 0) + (trial.result.usage.tokens_out or 0)
