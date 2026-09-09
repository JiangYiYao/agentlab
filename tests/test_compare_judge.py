from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from agentlab.cli import main
from agentlab.evaluation.compare import _blind_mapping, _compare_stdin
from agentlab.models import Trial
from agentlab.records.runs import latest_run_id
from agentlab.schema import Case, Cell, Variant, judge_call_count, judge_mode
from agentlab.validate import load_experiment as load_exp
from tests.helpers import make_min_exp


def test_compare_case_call_count(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["judge"] = {"command": ["true"], "mode": "compare_case"}
    data["concerns"].append(
        {
            "id": "gold",
            "intent": "looks right",
            "role": "objective",
            "measure": {"type": "llm_rubric"},
        }
    )
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    exp = load_exp(dest)
    assert judge_mode(exp) == "compare_case"
    assert judge_call_count(exp) == 1


def test_blind_mapping_hides_names() -> None:
    mapping = _blind_mapping(["baseline", "treatment"], "salt")
    assert set(mapping.values()) == {"baseline", "treatment"}
    assert set(mapping) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    trial = Trial(
        id="t",
        variant=Variant(id="baseline", role="baseline", path="v"),
        cell=Cell(id="local-cli"),
        case=Case(id="smoke"),
        repeat=1,
        contract_hash="x",
        experiment_root=Path("/tmp"),
    )
    from agentlab.schema import Concern

    concern = Concern.model_validate(
        {
            "id": "gold",
            "intent": "x",
            "role": "objective",
            "measure": {"type": "llm_rubric"},
        }
    )
    stdin = _compare_stdin(trial, [concern], mapping, "be good", "only this task")
    assert "baseline" not in stdin
    assert "treatment" not in stdin
    assert "A" in stdin


def test_compare_case_run_applies_scores(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    dump = dest / "judge-dump.json"
    script = dest / "compare_echo.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "stdin = sys.stdin.read()\n"
        "patches = sorted(p.name for p in Path('patches').glob('*.diff'))\n"
        "Path(sys.argv[1]).write_text(json.dumps({'stdin': stdin, 'patches': patches}))\n"
        "print(json.dumps({"
        "'ranking': ['A', 'B'],"
        "'identical': [],"
        "'usable': {'A': True, 'B': True},"
        "'scores': {'A': {'gold': 9}, 'B': {'gold': 8}}"
        "}))\n",
        encoding="utf-8",
    )
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["matrix"]["cells"][0]["command"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('hello.txt').write_text('hi\\n')",
    ]
    data["judge"] = {
        "command": [sys.executable, str(script), str(dump)],
        "mode": "compare_case",
        "timeout_s": 30,
    }
    data["concerns"].append(
        {
            "id": "gold",
            "intent": "looks right",
            "role": "objective",
            "measure": {"type": "llm_rubric"},
        }
    )
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert main(["run", "--exp", str(dest)]) == 0
    run_id = latest_run_id(dest)
    assert run_id
    compares = list((dest / "runs" / run_id / "compare").glob("*"))
    assert len(compares) == 1
    mapping = json.loads((compares[0] / "mapping.json").read_text(encoding="utf-8"))
    stdin = (compares[0] / "stdin.md").read_text(encoding="utf-8")
    assert "baseline" not in stdin
    assert "treatment" not in stdin
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert "baseline" not in dumped["stdin"]
    assert "A.diff" in dumped["patches"]
    assert "B.diff" in dumped["patches"]
    for label, vid in mapping.items():
        scores = json.loads(
            (dest / "trials" / f"{vid}__local-cli__smoke__r1" / "scores.json").read_text(encoding="utf-8")
        )
        gold = next(item for item in scores if item["concern_id"] == "gold")
        assert gold["unknown"] is False
        assert gold["value"] == (9 if label == "A" else 8)
    report = (dest / "report.md").read_text(encoding="utf-8")
    assert "并排对比" in report
    assert "ranking=" in report
