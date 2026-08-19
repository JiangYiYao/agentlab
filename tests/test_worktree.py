from __future__ import annotations

import subprocess
from pathlib import Path

from agentlab.adapters.isolation.worktree import (
    WorktreeIsolation,
    cleanup_experiment_worktrees,
    ensure_git_repo,
    experiment_worktrees,
    git_common_dir,
    list_worktree_paths,
)
from agentlab.cli import main
from agentlab.models import Trial
from agentlab.schema import Case, Cell, Variant
from tests.helpers import make_min_exp


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


def test_cleanup_removes_experiment_worktrees_not_main(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    exp = tmp_path / "exp"
    trial = _trial(tmp_path)
    trial.experiment_root.mkdir()
    iso = WorktreeIsolation(repo=repo, freeze="HEAD")
    with iso.worktree_lock():
        sandbox = iso.create(trial)
    assert sandbox.root in experiment_worktrees(repo, exp)
    assert repo.resolve() in list_worktree_paths(repo)

    removed = cleanup_experiment_worktrees(repo, exp)
    assert sandbox.root.resolve() in [p.resolve() for p in removed]
    assert not sandbox.root.exists()
    assert repo.is_dir()
    assert (repo / "README").read_text(encoding="utf-8") == "hi\n"
    assert experiment_worktrees(repo, exp) == []
    assert repo.resolve() in list_worktree_paths(repo)


def test_cli_cleanup_after_user_would_confirm(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dest = make_min_exp(tmp_path / "exp")
    text = dest.joinpath("experiment.yaml").read_text(encoding="utf-8")
    text = text.replace("type: tempdir", "type: git-worktree")
    if "repo:" not in text:
        text = text.replace(
            "type: git-worktree",
            f"type: git-worktree\n  repo: {repo}",
        )
    dest.joinpath("experiment.yaml").write_text(text, encoding="utf-8")

    sandbox = dest / "trials" / "kept" / "sandbox"
    sandbox.parent.mkdir(parents=True)
    subprocess.check_call(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(sandbox), "HEAD"]
    )
    (dest / "trials" / "kept" / "scores.json").write_text("[]\n", encoding="utf-8")
    assert sandbox.is_dir()

    assert main(["cleanup", "--exp", str(dest)]) == 0
    assert not sandbox.exists()
    assert repo.is_dir()
    assert sandbox.resolve() not in list_worktree_paths(repo)
