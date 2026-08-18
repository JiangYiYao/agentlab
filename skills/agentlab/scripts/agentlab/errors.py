from __future__ import annotations

from typing import Literal


class ContractError(Exception):
    """Validation-period contract error. CLI maps this to exit 2."""

    def __init__(self, code: str, message: str, *, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(message)

    def format_line(self) -> str:
        loc = f"{self.path}: " if self.path else ""
        return f"{loc}[{self.code}] {self.args[0]}"


class AdapterError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class TimeoutError(AdapterError):
    def __init__(self, message: str = "command timed out") -> None:
        super().__init__("command_timeout", message)


class IsolationLeakError(AdapterError):
    def __init__(self, message: str) -> None:
        super().__init__("isolation_leak", message)


class CommandCrashError(AdapterError):
    def __init__(self, message: str) -> None:
        super().__init__("command_nonzero", message)


class BudgetExceeded(AdapterError):
    def __init__(self, reason: Literal["timeout", "budget_tokens", "budget_usd", "budget_experiment"]) -> None:
        self.reason = reason
        super().__init__("budget_exceeded", reason)
