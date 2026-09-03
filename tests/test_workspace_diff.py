from __future__ import annotations

import json
from pathlib import Path

from agentlab.adapters.evaluator.builtin import _workspace_diff
from agentlab.adapters.isolation.worktree import WorktreeIsolation, ensure_git_repo
from agentlab.models import Sandbox, Trial
from agentlab.schema import Case, Cell, Concern, Measure, Variant
from agentlab.workspace import hash_snapshot


def _trial(tmp: Path, project: Path) -> Trial:
    exp = tmp / "exp"
    tid = "baseline__local-cli__smoke__r1"
    tdir = exp / "trials" / tid
    tdir.mkdir(parents=True)
    trial = Trial(
        id=tid,
        variant=Variant(id="baseline", role="baseline", path="v"),
        cell=Cell(id="local-cli"),
        case=Case(id="smoke"),
        repeat=1,
        contract_hash="x",
        experiment_root=exp,
        sandbox=Sandbox(root=project, project_root=project, worktree=(project / ".git").exists()),
    )
    return trial


def _concern(allow: list[str] | None = None) -> Concern:
    return Concern.model_validate(
        {
            "id": "scope",
            "intent": "x",
            "role": "gate",
            "measure": {"type": "workspace_diff", "allow_write": allow},
            "pass": {"op": "==", "vs": "value", "value": True},
            "aggregate": "all_pass",
        }
    )


def test_hash_diff_modify_and_delete(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.txt").write_text("old\n", encoding="utf-8")
    (project / "b.txt").write_text("keep\n", encoding="utf-8")
    trial = _trial(tmp_path, project)
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(project)}), encoding="utf-8"
    )
    (project / "a.txt").write_text("new\n", encoding="utf-8")
    (project / "b.txt").unlink()
    (project / "c.txt").write_text("add\n", encoding="utf-8")
    ok, evidence = _workspace_diff(trial, _concern(), {})
    statuses = {item["path"]: item["status"] for item in evidence["changed"]}
    assert statuses["a.txt"] == "M"
    assert statuses["b.txt"] == "D"
    assert statuses["c.txt"] == "A"
    assert ok is True


def test_allow_write_rejects_modified_outside_list(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ok.txt").write_text("1\n", encoding="utf-8")
    (project / "nope.txt").write_text("1\n", encoding="utf-8")
    trial = _trial(tmp_path, project)
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(project)}), encoding="utf-8"
    )
    (project / "nope.txt").write_text("2\n", encoding="utf-8")
    ok, evidence = _workspace_diff(trial, _concern(["ok.txt"]), {})
    assert ok is False
    assert any(item["path"] == "nope.txt" for item in evidence["bad"])


def test_git_and_nested_repo_modifications(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README").write_text("hi\n", encoding="utf-8")
    ensure_git_repo(root)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "app.txt").write_text("app\n", encoding="utf-8")
    ensure_git_repo(nested)
    trial = _trial(tmp_path, root)
    iso = WorktreeIsolation(
        repo=root,
        freeze="HEAD",
        nested_repos=[{"path": "repos/app", "source": str(nested), "freeze": "HEAD"}],
        experiment_root=trial.experiment_root,
    )
    with iso.worktree_lock():
        sandbox = iso.create(trial)
    trial.sandbox = sandbox
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(sandbox.project_root)}), encoding="utf-8"
    )
    (sandbox.project_root / "README").write_text("changed\n", encoding="utf-8")
    (sandbox.project_root / "repos" / "app" / "app.txt").write_text("changed\n", encoding="utf-8")
    ok, evidence = _workspace_diff(trial, _concern(), {})
    paths = {item["path"]: item["status"] for item in evidence["changed"]}
    assert paths.get("README") == "M"
    assert any(path.endswith("app.txt") and status == "M" for path, status in paths.items())
    assert ok is True
    with iso.worktree_lock():
        iso.destroy(sandbox)


def test_untracked_is_not_added(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README").write_text("hi\n", encoding="utf-8")
    ensure_git_repo(root)
    trial = _trial(tmp_path, root)
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(root)}), encoding="utf-8"
    )
    (root / "scratch.log").write_text("tmp\n", encoding="utf-8")
    ok, evidence = _workspace_diff(trial, _concern(), {})
    statuses = {item["path"]: item["status"] for item in evidence["changed"]}
    assert statuses.get("scratch.log") == "U"
    assert ok is True


def test_preexisting_git_dirty_not_attributed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README").write_text("hi\n", encoding="utf-8")
    ensure_git_repo(root)
    (root / "README").write_text("already dirty\n", encoding="utf-8")
    trial = _trial(tmp_path, root)
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(root)}), encoding="utf-8"
    )
    ok, evidence = _workspace_diff(trial, _concern(), {})
    assert evidence["changed"] == []
    assert ok is True


def test_same_content_rename_is_r(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "old.txt").write_text("same-bytes\n", encoding="utf-8")
    trial = _trial(tmp_path, project)
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(project)}), encoding="utf-8"
    )
    (project / "old.txt").unlink()
    (project / "new.txt").write_text("same-bytes\n", encoding="utf-8")
    ok, evidence = _workspace_diff(trial, _concern(), {})
    renamed = [item for item in evidence["changed"] if item["status"] == "R"]
    assert len(renamed) == 1
    assert renamed[0]["path"] == "new.txt"
    assert renamed[0]["from"] == "old.txt"
    assert ok is True


def test_nested_rename_from_is_project_relative(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README").write_text("hi\n", encoding="utf-8")
    ensure_git_repo(root)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "app.txt").write_text("app\n", encoding="utf-8")
    ensure_git_repo(nested)
    trial = _trial(tmp_path, root)
    iso = WorktreeIsolation(
        repo=root,
        freeze="HEAD",
        nested_repos=[{"path": "repos/app", "source": str(nested), "freeze": "HEAD"}],
        experiment_root=trial.experiment_root,
    )
    with iso.worktree_lock():
        sandbox = iso.create(trial)
    trial.sandbox = sandbox
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(sandbox.project_root)}), encoding="utf-8"
    )
    src = sandbox.project_root / "repos" / "app" / "app.txt"
    dest = sandbox.project_root / "repos" / "app" / "renamed.txt"
    src.replace(dest)
    ok, evidence = _workspace_diff(trial, _concern(), {})
    renamed = [item for item in evidence["changed"] if item["status"] == "R"]
    assert renamed
    assert renamed[0]["from"].endswith("app.txt")
    assert renamed[0]["path"].endswith("renamed.txt")
    assert renamed[0]["from"].startswith("repos/")
    assert renamed[0]["path"].startswith("repos/")
    assert ok is True
    with iso.worktree_lock():
        iso.destroy(sandbox)


def test_write_meta_keeps_workspace_snap(tmp_path: Path) -> None:
    from agentlab.scheduler import _write_meta

    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.txt").write_text("x\n", encoding="utf-8")
    trial = _trial(tmp_path, project)
    trial.trial_dir().mkdir(parents=True, exist_ok=True)
    snap = hash_snapshot(project)
    _write_meta(trial, {"workspace_snap": snap, "worktree": False}, score_basis="abc")
    _write_meta(trial, {"phase": "preparing"}, score_basis="abc")
    _write_meta(trial, {"pid": 1, "pgid": 1, "phase": "running"}, score_basis="abc")
    meta = json.loads((trial.trial_dir() / "meta.json").read_text(encoding="utf-8"))
    assert meta["workspace_snap"] == snap
    assert meta["phase"] == "running"
    assert meta["worktree"] is False
