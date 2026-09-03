from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from agentlab.judge import spawn_judge
from agentlab.models import Trial
from agentlab.schema import Experiment


def test_judge_timeout_is_unavailable(tmp_path: Path) -> None:
    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    exp, trial = _judge_exp(tmp_path, [sys.executable, str(hang)])
    score = spawn_judge(trial, exp.concerns[0], exp, 1)
    assert score.unknown is True
    assert score.evidence.get("error_code") == "judge_unavailable"


def _judge_exp(tmp_path: Path, command: list[str]) -> tuple[Experiment, Trial]:
    (tmp_path / "criteria.md").write_text("## gold\nbe good\n", encoding="utf-8")
    exp = Experiment.model_validate(
        {
            "schema_version": 1,
            "id": "judge-t",
            "name": "judge-t",
            "artifact": {"type": "dir", "name": "judge-t"},
            "criteria": {"path": "criteria.md", "sha256": "ab" * 32},
            "variants": [{"id": "baseline", "role": "baseline", "path": "v"}],
            "concerns": [
                {
                    "id": "gold",
                    "intent": "x",
                    "role": "objective",
                    "measure": {"type": "llm_rubric"},
                    "judge": {"command": command, "timeout_s": 1},
                }
            ],
            "matrix": {"cells": [{"id": "local-cli", "command": ["true"]}]},
            "cases": [{"id": "main"}],
            "isolation": {"type": "tempdir"},
            "budget": {"max_trials": 8},
        }
    )
    trial = Trial(
        id="t",
        variant=exp.variants[0],
        cell=exp.matrix.cells[0],
        case=exp.cases[0],
        repeat=1,
        contract_hash="x",
        experiment_root=tmp_path,
    )
    return exp, trial


def test_judge_timeout_kills_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    hang = tmp_path / "hang.py"
    hang.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    exp, trial = _judge_exp(tmp_path, [sys.executable, str(hang), str(pid_file)])
    score = spawn_judge(trial, exp.concerns[0], exp, 1)
    assert score.evidence.get("error_code") == "judge_unavailable"
    deadline = time.time() + 12
    pid = None
    while time.time() < deadline:
        if pid_file.is_file():
            text = pid_file.read_text(encoding="utf-8").strip()
            if text:
                pid = int(text)
                break
        time.sleep(0.05)
    assert pid is not None
    deadline = time.time() + 12
    alive = True
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
            break
        time.sleep(0.05)
    assert alive is False


def test_judge_non_bool_pass_is_bad(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "print('{\"concern_id\":\"gold\",\"value\":1,\"pass\":\"yes\",\"unknown\":false}')\n",
        encoding="utf-8",
    )
    exp, trial = _judge_exp(tmp_path, [sys.executable, str(bad)])
    score = spawn_judge(trial, exp.concerns[0], exp, 5)
    assert score.unknown is True
    assert score.evidence.get("error_code") == "judge_bad_stdout"
