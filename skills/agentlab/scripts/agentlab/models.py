from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab.schema import Case, Cell, Experiment, Variant


@dataclass
class Sandbox:
    root: Path
    project_root: Path
    home: Path | None = None
    worktree: bool = False


@dataclass
class Usage:
    tokens_in: int | None = None
    tokens_out: int | None = None
    usd: float | None = None
    tokens_unknown: bool = True
    usd_unknown: bool = True


@dataclass
class RunnerResult:
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    usage: Usage
    wall_clock_s: float
    killed_reason: str | None = None
    error_code: str | None = None


@dataclass
class Score:
    concern_id: str
    value: Any = None
    unit: str | None = None
    pass_: bool | None = None
    soft: bool = False
    unknown: bool = False
    n: int = 1
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "concern_id": self.concern_id,
            "value": self.value,
            "unit": self.unit,
            "pass": self.pass_,
            "soft": self.soft,
            "unknown": self.unknown,
            "n": self.n,
            "evidence": self.evidence,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Score:
        return cls(
            concern_id=str(data.get("concern_id", "")),
            value=data.get("value"),
            unit=data.get("unit"),
            pass_=data.get("pass"),
            soft=bool(data.get("soft", False)),
            unknown=bool(data.get("unknown", False)),
            n=int(data.get("n", 1)),
            evidence=dict(data.get("evidence") or {}),
        )


@dataclass
class Trial:
    id: str
    variant: Variant
    cell: Cell
    case: Case
    repeat: int
    contract_hash: str
    experiment_root: Path
    freeze_sha: str | None = None
    sandbox: Sandbox | None = None
    result: RunnerResult | None = None
    scores: list[Score] = field(default_factory=list)
    error_code: str | None = None
    killed_reason: str | None = None
    skipped: bool = False
    reused: bool = False
    retried: bool = False
    reused_from: str | None = None

    def trial_dir(self) -> Path:
        return self.experiment_root / "trials" / self.id

    def outputs_dir(self) -> Path:
        return self.trial_dir() / "outputs"

    def program_root(self, exp: Experiment, sandbox: Sandbox) -> Path:
        if exp.artifact.layout == "inplace":
            return sandbox.project_root
        return self.outputs_dir() / "program"
