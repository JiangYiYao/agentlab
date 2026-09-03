from __future__ import annotations

import sys
from pathlib import Path

from agentlab.judge import spawn_judge
from agentlab.models import Trial
from agentlab.schema import Case, Cell, Concern, Experiment, JudgeSpec, Variant


def test_judge_timeout_is_unavailable(tmp_path: Path) -> None:
    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
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
                    "judge": {"command": [sys.executable, str(hang)], "timeout_s": 1},
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
    score = spawn_judge(trial, exp.concerns[0], exp, 1)
    assert score.unknown is True
    assert score.evidence.get("error_code") == "judge_unavailable"
