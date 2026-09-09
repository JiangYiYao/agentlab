from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from agentlab.errors import BudgetExceeded
from agentlab.models import Trial, Usage
from agentlab.schema import Budget


class BudgetTracker:
    """Reserve before dispatch, reconcile on completion, serialize accounting."""
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.started = time.time()
        self.used_usd = 0.0
        self.used_tokens = 0
        self.running = 0
        self.reserved_usd = 0.0
        self.reserved_tokens = 0
        self.exceeded_reason: str | None = None
        self.unknown_usage = 0
        self._condition = threading.Condition()

    def exceeded(self) -> bool:
        with self._condition:
            if self.budget.wall_clock_s is not None and time.time() >= self.started + self.budget.wall_clock_s:
                self.exceeded_reason = "budget_experiment"
            if self.budget.usd is not None and self.used_usd > self.budget.usd:
                self.exceeded_reason = "budget_usd"
            if self.budget.tokens is not None and self.used_tokens > self.budget.tokens:
                self.exceeded_reason = "budget_tokens"
            return self.exceeded_reason is not None

    def trial_deadline(self, kind: str = "trial") -> float | None:
        limits = self.budget.per_judge if kind == "judge" else self.budget.per_trial
        deadlines = []
        if self.budget.wall_clock_s is not None:
            deadlines.append(self.started + self.budget.wall_clock_s)
        if limits.wall_clock_s is not None:
            deadlines.append(time.time() + limits.wall_clock_s)
        return min(deadlines) if deadlines else None

    @contextmanager
    def watch(self, kind, get_usage):
        limits = self.budget.per_judge if kind == "judge" else self.budget.per_trial
        usd, tokens = limits.usd or 0, limits.tokens or 0
        with self._condition:
            while True:
                if self.exceeded():
                    raise BudgetExceeded(self.exceeded_reason)
                reason = None
                if self.budget.usd is not None and self.used_usd + self.reserved_usd + usd > self.budget.usd:
                    reason = "budget_usd"
                if self.budget.tokens is not None and self.used_tokens + self.reserved_tokens + tokens > self.budget.tokens:
                    reason = "budget_tokens"
                if reason is None:
                    break
                if not self.running:
                    self.exceeded_reason = reason
                    raise BudgetExceeded(reason)
                self._condition.wait(timeout=0.1)
            self.running += 1
            self.reserved_usd += usd
            self.reserved_tokens += tokens
        try:
            yield
        finally:
            usage = get_usage() or Usage()
            with self._condition:
                self.running -= 1
                self.reserved_usd -= usd
                self.reserved_tokens -= tokens
                actual_tokens = None if usage.tokens_in is None and usage.tokens_out is None else (usage.tokens_in or 0) + (usage.tokens_out or 0)
                self.used_usd += usage.usd if usage.usd is not None else usd
                self.used_tokens += actual_tokens if actual_tokens is not None else tokens
                if (self.budget.usd is not None and usage.usd is None) or (self.budget.tokens is not None and actual_tokens is None):
                    self.unknown_usage += 1
                self.exceeded()
                self._condition.notify_all()

    @contextmanager
    def trial_watch(self, trial: Trial):
        with self.watch("trial", lambda: trial.result.usage if trial.result else None):
            yield
