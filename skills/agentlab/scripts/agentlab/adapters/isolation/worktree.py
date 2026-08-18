from __future__ import annotations

import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from agentlab.errors import AdapterError
from agentlab.flock import exclusive
from agentlab.models import Sandbox, Trial


def git_common_dir(repo: Path) -> Path:
    out = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
        text=True,
    ).strip()
    path = Path(out)
    if not path.is_absolute():
        path = (repo / path).resolve()
    return path


def resolve_freeze_sha(repo: Path, freeze: str | None) -> str:
    ref = freeze or "HEAD"
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", ref], text=True).strip()


def ensure_git_repo(repo: Path) -> None:
    if (repo / ".git").exists():
        return
    subprocess.check_call(["git", "init"], cwd=repo)
    subprocess.check_call(["git", "add", "-A"], cwd=repo)
    subprocess.check_call(
        ["git", "-c", "user.email=ci@agentlab", "-c", "user.name=agentlab", "commit", "-m", "fixture"],
        cwd=repo,
    )


def prune_worktrees(repo: Path) -> None:
    try:
        subprocess.check_call(["git", "-C", str(repo), "worktree", "prune"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


class WorktreeIsolation:
    type = "git-worktree"

    def __init__(self, repo: Path | None = None, freeze: str | None = None, subdir: str = ".") -> None:
        self.repo = repo
        self.freeze = freeze
        self.subdir = subdir

    def lock_path(self) -> Path:
        if self.repo is None:
            raise AdapterError("sandbox_create_failed", "worktree missing repo")
        return git_common_dir(self.repo) / "agentlab-worktree.lock"

    def worktree_lock(self):
        return exclusive(self.lock_path())

    def create(self, trial: Trial) -> Sandbox:
        if self.repo is None:
            raise AdapterError("sandbox_create_failed", "worktree missing repo")
        ensure_git_repo(self.repo)
        dest = trial.trial_dir() / "sandbox"
        if dest.exists() and any(dest.iterdir()):
            raise AdapterError("sandbox_create_failed", f"worktree dest not empty: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        sha = resolve_freeze_sha(self.repo, self.freeze)
        trial.freeze_sha = sha
        try:
            subprocess.check_call(
                ["git", "-C", str(self.repo), "worktree", "add", "--detach", str(dest), sha],
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            prune_worktrees(self.repo)
            raise AdapterError("sandbox_create_failed", str(exc)) from exc
        project = dest if self.subdir in {".", ""} else dest / self.subdir
        return Sandbox(root=dest, project_root=project, home=None, worktree=True)

    def destroy(self, sandbox: Sandbox) -> None:
        if self.repo is None:
            shutil.rmtree(sandbox.root, ignore_errors=True)
            return
        try:
            subprocess.check_call(
                ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(sandbox.root)]
            )
        except subprocess.CalledProcessError:
            prune_worktrees(self.repo)
            shutil.rmtree(sandbox.root, ignore_errors=True)
