from __future__ import annotations

from pathlib import Path

from agentlab.cli import main
from agentlab.runs import latest_run_id, load_manifest
from tests.helpers import make_min_exp


def test_only_variant_run_does_not_count_other_trials(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    assert main(["run", "--exp", str(dest)]) == 0
    first = latest_run_id(dest)
    assert first
    first_manifest = load_manifest(dest, first)
    assert first_manifest is not None
    assert len(first_manifest["planned"]) == 2

    assert main(["run", "--exp", str(dest), "--only-variant", "treatment"]) == 0
    second = latest_run_id(dest)
    assert second != first
    manifest = load_manifest(dest, second)
    assert manifest is not None
    assert len(manifest["planned"]) == 1
    report = (dest / "report.md").read_text(encoding="utf-8")
    assert "planned: 1" in report
    assert "scored: 1" in report
    assert "/ `smoke` / r1:" in report


def test_repetitions_override_does_not_edit_yaml(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    before = (dest / "experiment.yaml").read_text(encoding="utf-8")
    assert main(["run", "--exp", str(dest), "--repetitions", "1", "--only-variant", "baseline"]) == 0
    assert (dest / "experiment.yaml").read_text(encoding="utf-8") == before
    manifest = load_manifest(dest, latest_run_id(dest) or "")
    assert manifest is not None
    assert manifest["overrides"]["repetitions"] == 1
    assert len(manifest["planned"]) == 1


def test_status_lists_latest_run(tmp_path: Path, capsys) -> None:
    dest = make_min_exp(tmp_path / "exp")
    assert main(["run", "--exp", str(dest), "--only-variant", "baseline"]) == 0
    capsys.readouterr()
    assert main(["status", "--exp", str(dest)]) == 0
    out = capsys.readouterr().out
    assert "run_id:" in out
    assert "planned: 1" in out
    assert "phase=" in out
