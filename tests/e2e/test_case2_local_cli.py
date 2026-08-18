from __future__ import annotations

import shutil
from pathlib import Path

import yaml

import pytest

from agentlab.cli import main

CASE2 = Path(__file__).resolve().parents[2] / "examples" / "case2"

pytestmark = pytest.mark.skipif(not CASE2.is_dir(), reason="examples/case2 is local-only")


def test_case2_gate_and_report() -> None:
    assert main(["brief", "--exp", str(CASE2)]) == 0
    assert main(["run", "--exp", str(CASE2), "--gate"]) == 0
    assert main(["report", "--exp", str(CASE2)]) == 0
    report = (CASE2 / "report.md").read_text(encoding="utf-8")
    assert "关注点" in report
    assert "sections-present" in report
    assert "counterarg-inline" in report


def test_case2_upgrade_negative(tmp_path: Path) -> None:
    dest = tmp_path / "case2"
    shutil.copytree(CASE2, dest)
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["matrix"]["cells"][0]["command"] = [
        "bash",
        "${experiment_root}/cases/_eval/fake_trading_agent_upgrade.sh",
    ]
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert main(["brief", "--exp", str(dest), "--confirm-criteria"]) == 0
    assert main(["run", "--exp", str(dest), "--gate"]) == 1
