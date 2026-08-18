from __future__ import annotations

import shutil
from contextlib import nullcontext
from pathlib import Path

from agentlab.errors import ContractError
from agentlab.models import Sandbox, Trial


class HomedirIsolation:
    type = "homedir"

    def create(self, trial: Trial, *, inherit_host_identity: bool = True) -> Sandbox:
        root = trial.trial_dir() / "sandbox"
        if root.exists():
            shutil.rmtree(root)
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        home = None
        if not inherit_host_identity:
            home = root / "home"
            home.mkdir(parents=True, exist_ok=True)
        scutio_home = trial.outputs_dir() / "scutio-home"
        scutio_home.mkdir(parents=True, exist_ok=True)
        if str(scutio_home.resolve()).startswith("/tmp"):
            raise ContractError("scutio_home_unstable_path", "SCUTIO_HOME must not be under /tmp")
        return Sandbox(root=root, project_root=workspace, home=home, worktree=False)

    def destroy(self, sandbox: Sandbox) -> None:
        if sandbox.root.exists():
            shutil.rmtree(sandbox.root, ignore_errors=True)

    def worktree_lock(self):
        return nullcontext()
