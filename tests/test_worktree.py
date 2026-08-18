from __future__ import annotations

import subprocess
from pathlib import Path

from agentlab.adapters.isolation.worktree import WorktreeIsolation, ensure_git_repo, git_common_dir
from agentlab.models import Trial
from agentlab.schema import Case, Cell, Variant


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "README").write_text("hi\n", encoding="utf-8")
    ensure_git_repo(repo)
    return repo


def _trial(tmp: Path) -> Trial:
    return Trial(
        id="baseline__local-cli__smoke__r1",
        variant=Variant(id="baseline", role="baseline", path="variants/baseline"),
        cell=Cell(id="local-cli"),
        case=Case(id="smoke"),
        repeat=1,
        contract_hash="sha256:x",
        experiment_root=tmp / "exp",
    )


def test_worktree_add_and_project_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    trial = _trial(tmp_path)
    trial.experiment_root.mkdir()
    iso = WorktreeIsolation(repo=repo, freeze="HEAD", subdir=".")
    with iso.worktree_lock():
        sandbox = iso.create(trial)
    assert sandbox.project_root.is_dir()
    assert (sandbox.project_root / "README").read_text(encoding="utf-8") == "hi\n"
    assert sandbox.worktree is True
    assert trial.freeze_sha
    with iso.worktree_lock():
        iso.destroy(sandbox)
    assert not sandbox.root.exists()


def test_worktree_lock_uses_common_dir(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    iso = WorktreeIsolation(repo=repo)
    common = git_common_dir(repo)
    assert iso.lock_path() == common / "agentlab-worktree.lock"


def test_submodule_gitfile_lock(tmp_path: Path) -> None:
    real = tmp_path / "real.git"
    work = tmp_path / "sub"
    work.mkdir()
    (work / "b").write_text("b\n", encoding="utf-8")
    subprocess.check_call(["git", "init", "--bare", str(real)])
    subprocess.check_call(["git", "init"], cwd=work)
    subprocess.check_call(["git", "add", "-A"], cwd=work)
    subprocess.check_call(
        ["git", "-c", "user.email=ci@agentlab", "-c", "user.name=agentlab", "commit", "-m", "fixture"],
        cwd=work,
    )
    common = git_common_dir(work)
    # Simulate a submodule checkout whose .git is a gitfile, not a directory.
    gitfile = work / ".git"
    if gitfile.is_dir():
        # rewrite as a gitfile pointing at the real common dir
        (work / ".git-file").write_text(f"gitdir: {common}\n", encoding="utf-8")
    iso = WorktreeIsolation(repo=work)
    assert iso.lock_path() == git_common_dir(work) / "agentlab-worktree.lock"
    assert iso.lock_path().parent.is_dir()
