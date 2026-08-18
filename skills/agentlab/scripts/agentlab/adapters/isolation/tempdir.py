from __future__ import annotations

import shutil
from contextlib import nullcontext
from pathlib import Path

from agentlab.models import Sandbox, Trial


class TempdirIsolation:
    type = "tempdir"

    def create(self, trial: Trial) -> Sandbox:
        root = trial.trial_dir() / "sandbox"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        return Sandbox(root=root, project_root=root, home=None, worktree=False)

    def destroy(self, sandbox: Sandbox) -> None:
        if sandbox.root.exists():
            shutil.rmtree(sandbox.root, ignore_errors=True)

    def worktree_lock(self):
        return nullcontext()
