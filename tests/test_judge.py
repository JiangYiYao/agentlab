from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from agentlab.judge import criteria_for_judge, extract_json_payload, spawn_judge
from agentlab.models import Sandbox, Trial
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


def test_extract_json_strips_fence_and_trailing() -> None:
    fenced = 'note\n```json\n{"concern_id":"gold","value":6,"pass":true,"unknown":false}\n```\n'
    payload = extract_json_payload(fenced)
    assert payload["value"] == 6
    trailing = '{"concern_id":"gold","value":9,"pass":true,"unknown":false}\nWARN done\n'
    payload = extract_json_payload(trailing)
    assert payload["value"] == 9


def test_criteria_for_judge_keeps_preface(tmp_path: Path) -> None:
    (tmp_path / "criteria.md").write_text(
        "# 标准\n\n仓里只有两个任务，按用例评，不要另找开关。\n\n## gold\nbe good\n\n## other\nignore\n",
        encoding="utf-8",
    )
    text = criteria_for_judge(tmp_path, "gold")
    assert "不要另找开关" in text
    assert "be good" in text
    assert "ignore" not in text


def test_judge_stdin_has_case_and_cwd_is_workspace(tmp_path: Path) -> None:
    dump = tmp_path / "dump"
    dump.mkdir()
    script = tmp_path / "echo_judge.py"
    script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text(sys.stdin.read())\n"
        "Path(sys.argv[2]).write_text(os.getcwd())\n"
        "print(json.dumps({'concern_id':'gold','value':1,'pass':True,'unknown':False}))\n",
        encoding="utf-8",
    )
    (tmp_path / "cases" / "main").mkdir(parents=True)
    (tmp_path / "cases" / "main" / "prompt.md").write_text("only clean FOO_KEY=1\n", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "src.txt").write_text("code\n", encoding="utf-8")
    exp, trial = _judge_exp(tmp_path, [sys.executable, str(script), str(dump / "stdin.txt"), str(dump / "cwd.txt")])
    trial.sandbox = Sandbox(root=project, project_root=project)
    score = spawn_judge(trial, exp.concerns[0], exp, 5)
    assert score.unknown is False
    assert score.value == 1
    stdin = (dump / "stdin.txt").read_text(encoding="utf-8")
    assert "FOO_KEY=1" in stdin
    assert "case_id: main" in stdin
    assert "concern_id: gold" in stdin
    cwd = Path((dump / "cwd.txt").read_text(encoding="utf-8").strip())
    assert cwd.name == "workspace"
    assert (cwd / "src.txt").is_file()
    opaque_dir = next((tmp_path / "trials" / ".judge").iterdir())
    assert (opaque_dir / "stdout.log").is_file()
    assert (opaque_dir / "prompt.md").read_text(encoding="utf-8") == "only clean FOO_KEY=1\n"


def test_judge_markdown_fence_is_parsed(tmp_path: Path) -> None:
    script = tmp_path / "fence.py"
    script.write_text(
        "print('```json')\n"
        "print('{\"concern_id\":\"gold\",\"value\":6,\"pass\":true,\"unknown\":false}')\n"
        "print('```')\n",
        encoding="utf-8",
    )
    exp, trial = _judge_exp(tmp_path, [sys.executable, str(script)])
    score = spawn_judge(trial, exp.concerns[0], exp, 5)
    assert score.unknown is False
    assert score.value == 6
