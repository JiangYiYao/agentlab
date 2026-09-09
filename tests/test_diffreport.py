from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from agentlab.adapters.isolation.worktree import ensure_git_repo
from agentlab.cli import main
from agentlab.reporting.changes import collect_patches, render_trial_html, write_run_diff, write_trial_diff
from agentlab.models import Sandbox, Trial
from agentlab.records.runs import latest_run_id
from agentlab.schema import Case, Cell, Variant
from agentlab.execution.workspace import hash_snapshot
from tests.helpers import make_min_exp


def _trial(tmp: Path, project: Path) -> Trial:
    exp = tmp / "exp"
    tid = "baseline__local-cli__smoke__r1"
    tdir = exp / "trials" / tid
    tdir.mkdir(parents=True)
    return Trial(
        id=tid,
        variant=Variant(id="baseline", role="baseline", path="v"),
        cell=Cell(id="local-cli"),
        case=Case(id="smoke"),
        repeat=1,
        contract_hash="x",
        experiment_root=exp,
        sandbox=Sandbox(root=project, project_root=project),
    )


def test_added_file_html_lists_path_and_line(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    snap = hash_snapshot(project)
    (project / "hello.txt").write_text("hi there\n", encoding="utf-8")
    bundle = collect_patches(project, snap)
    assert any(item.path == "hello.txt" and item.status in {"A", "U"} for item in bundle.files)
    html = render_trial_html("t1", "baseline / local-cli / smoke / r1", bundle)
    assert "hello.txt" in html
    assert "hi there" in html
    assert "新增" in html or "未跟踪" in html
    assert "<script>" not in html


def test_html_escapes_content(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    snap = hash_snapshot(project)
    (project / "x.txt").write_text("<script>alert(1)</script>\n", encoding="utf-8")
    html = render_trial_html("t1", "x", collect_patches(project, snap))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_git_modify_shows_old_and_new(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README").write_text("old\n", encoding="utf-8")
    ensure_git_repo(repo)
    snap = hash_snapshot(repo)
    (repo / "README").write_text("new\n", encoding="utf-8")
    bundle = collect_patches(repo, snap)
    html = render_trial_html("t1", "x", bundle)
    assert "README" in html
    assert "old" in html
    assert "new" in html
    assert "修改" in html


def test_empty_workspace_says_no_changes(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.txt").write_text("same\n", encoding="utf-8")
    snap = hash_snapshot(project)
    html = render_trial_html("t1", "x", collect_patches(project, snap))
    assert "这次没有改文件" in html


def test_write_trial_and_run_index(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    trial = _trial(tmp_path, project)
    (trial.trial_dir() / "meta.json").write_text(
        json.dumps({"workspace_snap": hash_snapshot(project)}), encoding="utf-8"
    )
    (project / "n.txt").write_text("n\n", encoding="utf-8")
    dest = write_trial_diff(trial)
    assert dest is not None and dest.is_file()
    assert (trial.outputs_dir() / "diff.json").is_file()
    patch = (trial.outputs_dir() / "workspace.diff").read_text(encoding="utf-8")
    assert "n.txt" in patch
    run_id = "run1"
    archived = tmp_path / "exp" / "runs" / run_id / "trials" / trial.id / "outputs"
    archived.mkdir(parents=True)
    (archived / "diff.json").write_text((trial.outputs_dir() / "diff.json").read_text(encoding="utf-8"))
    (archived / "diff.html").write_text(dest.read_text(encoding="utf-8"))
    index = write_run_diff(tmp_path / "exp", run_id, [trial.id])
    assert index is not None
    text = index.read_text(encoding="utf-8")
    assert trial.id in text
    assert "baseline" in text
    assert f"trials/{trial.id}/outputs/diff.html" in text


def test_run_emits_diff_html(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["matrix"]["cells"][0]["command"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('hello.txt').write_text('hi\\n')",
    ]
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert main(["run", "--exp", str(dest), "--only-variant", "baseline"]) == 0
    trial_html = dest / "trials" / "baseline__local-cli__smoke__r1" / "outputs" / "diff.html"
    assert trial_html.is_file()
    body = trial_html.read_text(encoding="utf-8")
    assert "hello.txt" in body
    assert "hi" in body
    run_id = latest_run_id(dest)
    assert run_id
    index = dest / "runs" / run_id / "diff.html"
    assert index.is_file()
    report = (dest / "report.md").read_text(encoding="utf-8")
    assert f"runs/{run_id}/diff.html" in report
    archived = dest / "runs" / run_id / "trials" / "baseline__local-cli__smoke__r1" / "outputs" / "diff.html"
    assert archived.is_file()
