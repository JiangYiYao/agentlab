from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab.schema import Case, Cell, Experiment, Variant


SYSTEM_GATES = ["__isolation_leak__", "__wrong_skill_tree__"]


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
    execution_id: str | None = None
    execution_basis: str | None = None
    sandbox_path: Path | None = None
    measurement_basis: dict[str, str] = field(default_factory=dict)
    rescored: bool = False
    stage_times: dict[str, float] = field(default_factory=dict)
    budget_tracker: Any = None
    cached_scores: dict[str, Score] = field(default_factory=dict)
    force_score: bool = False
    compare_basis: str | None = None
    run_id: str | None = None
    evaluation_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    previous_evaluations: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record_evaluation(self, concern_id: str, kind: str, status: str, *, reason: str | None = None) -> None:
        previous = self.previous_evaluations.get(concern_id, {})
        self.evaluation_events[concern_id] = {
            "kind": kind, "status": status,
            "source_run": (previous.get("source_run") or self.reused_from) if status == "reused" else self.run_id,
            "source_verified": bool(previous.get("source_verified")) if status == "reused" else True,
            "reason": reason,
        }

    def trial_dir(self) -> Path:
        return self.experiment_root / "trials" / self.id

    def outputs_dir(self) -> Path:
        return self.trial_dir() / "outputs"

    def program_root(self, exp: Experiment, sandbox: Sandbox) -> Path:
        if exp.artifact.layout == "inplace":
            return sandbox.project_root
        return self.outputs_dir() / "program"
