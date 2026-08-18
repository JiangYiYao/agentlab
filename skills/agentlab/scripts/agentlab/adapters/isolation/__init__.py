from __future__ import annotations

from pathlib import Path

from agentlab.models import Sandbox, Trial
from agentlab.schema import Experiment, Isolation


def select_isolation(exp: Experiment, trial: Trial):
    kind = (trial.case.isolation.type if trial.case.isolation and trial.case.isolation.type else None) or exp.isolation.type
    if kind == "git-worktree":
        from agentlab.adapters.isolation.worktree import WorktreeIsolation

        return WorktreeIsolation()
    if kind == "homedir":
        from agentlab.adapters.isolation.homedir import HomedirIsolation

        return HomedirIsolation()
    from agentlab.adapters.isolation.tempdir import TempdirIsolation

    return TempdirIsolation()


def resolve_repo(exp: Experiment, root: Path) -> Path:
    spec = exp.isolation.repo or ""
    path = Path(spec)
    if not path.is_absolute():
        path = (root / spec).resolve()
    return path
