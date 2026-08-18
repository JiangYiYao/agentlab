from __future__ import annotations

import shutil
from pathlib import Path

from agentlab.cli import main
from agentlab.validate import load_experiment
from tests.helpers import make_min_exp


def test_brief_fixture_runnable(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    rc = main(["brief", "--exp", str(dest)])
    assert rc == 0
    brief = (dest / "brief.md").read_text(encoding="utf-8")
    assert "RUNNABLE: yes" in brief
    assert "contract_hash:" in brief


def test_brief_confirm_criteria(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    text = dest.joinpath("experiment.yaml").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("sha256:")]
    dest.joinpath("experiment.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["brief", "--exp", str(dest)]) == 2
    assert main(["brief", "--exp", str(dest), "--confirm-criteria"]) == 0
    exp = load_experiment(dest)
    assert exp.criteria.sha256
    assert len(exp.criteria.sha256) == 64


def test_brief_init_from(tmp_path: Path) -> None:
    src = tmp_path / "skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# hello\n", encoding="utf-8")
    dest = tmp_path / "new-exp"
    rc = main(["brief", "--exp", str(dest), "--init-from", str(src), "--confirm-criteria"])
    assert rc == 0
    assert (dest / "experiment.yaml").is_file()
    assert (dest / "criteria.md").is_file()
    assert (dest / "variants" / "baseline" / "SKILL.md").is_file()
    assert (dest / "brief.md").is_file()
    exp = load_experiment(dest)
    assert exp.isolation.type == "tempdir"
    assert exp.isolation.inherit_host_identity is True
    assert exp.criteria.sha256


def test_run_dry_expand(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    assert main(["run", "--exp", str(dest), "--dry-expand"]) == 0
