from __future__ import annotations

from pathlib import Path

from agentlab.gate import TrialRecord, evaluate_promotion
from agentlab.models import Score
from agentlab.report import render_report
from agentlab.schema import Experiment


def test_report_is_concern_by_cell(tmp_path: Path, monkeypatch) -> None:
    exp = Experiment.model_validate(
        {
            "schema_version": 1,
            "id": "rep",
            "name": "rep",
            "artifact": {"type": "dir", "name": "x"},
            "criteria": {"path": "criteria.md", "sha256": "ab" * 32},
            "variants": [
                {"id": "baseline", "role": "baseline", "path": "v/b"},
                {
                    "id": "treat",
                    "role": "treatment",
                    "path": "v/treat",
                    "hypothesis": {"change": "c", "bet": "b", "hurt": "h", "falsify": "f"},
                },
            ],
            "concerns": [
                {
                    "id": "gold",
                    "intent": "gold",
                    "role": "gate",
                    "scope": "case",
                    "measure": {"type": "gold_tree", "gold_dir": "gold"},
                    "pass": {"op": "==", "vs": "value", "value": True},
                    "aggregate": "all_pass",
                }
            ],
            "matrix": {"cells": [{"id": "local-cli", "command": ["true"]}]},
            "cases": [{"id": "smoke"}],
            "isolation": {"type": "tempdir"},
            "budget": {"max_trials": 4, "per_trial": {"wall_clock_s": 10}},
        }
    )
    recs = [
        TrialRecord(
            trial_id="t1",
            variant_id="treat",
            cell_id="local-cli",
            case_id="smoke",
            repeat=1,
            role="treatment",
            scores={
                "gold": Score(concern_id="gold", value=True, pass_=True),
                "__isolation_leak__": Score(concern_id="__isolation_leak__", value=True, pass_=True),
                "__wrong_skill_tree__": Score(concern_id="__wrong_skill_tree__", value=True, pass_=True),
            },
        )
    ]

    def fake_load(_exp, _root, *, trial_ids=None):
        return recs, []

    monkeypatch.setattr("agentlab.report.load_current_records", fake_load)
    text = render_report(exp, tmp_path)
    assert "## 关注点" in text
    assert "gold @ local-cli / smoke" in text
    assert "`treat` / `local-cli` / `smoke` / r1:" in text
    assert "综合分" not in text
