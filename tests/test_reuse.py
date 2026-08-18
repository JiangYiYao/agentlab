from __future__ import annotations

import json
from pathlib import Path

import yaml

from agentlab.cli import main
from agentlab.schema import fingerprint_score_basis
from agentlab.scheduler import load_current_records
from agentlab.validate import load_experiment, load_raw
from tests.helpers import make_min_exp


def test_second_run_skips_completed_baseline(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    assert main(["run", "--exp", str(dest)]) == 0
    base = dest / "trials" / "baseline__local-cli__smoke__r1"
    first = (base / "scores.json").read_text(encoding="utf-8")
    mtime = (base / "scores.json").stat().st_mtime_ns
    assert main(["run", "--exp", str(dest)]) == 0
    assert (base / "scores.json").read_text(encoding="utf-8") == first
    assert (base / "scores.json").stat().st_mtime_ns == mtime


def test_baseline_records_survive_new_treatment(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    assert main(["run", "--exp", str(dest)]) == 0
    exp = load_experiment(dest)
    meta = json.loads((dest / "trials" / "baseline__local-cli__smoke__r1" / "meta.json").read_text())
    assert meta.get("score_basis") == fingerprint_score_basis(exp)
    extra = dest / "variants" / "news-cap"
    extra.mkdir()
    (extra / "SKILL.md").write_text("# news-cap\n", encoding="utf-8")
    raw = load_raw(dest)
    raw["variants"].append(
        {
            "id": "news-cap",
            "role": "treatment",
            "path": "variants/news-cap",
            "parent": "baseline",
            "created_by": "manual",
            "hypothesis": {"change": "cap", "bet": "faster", "hurt": "miss", "falsify": "slower"},
        }
    )
    dest.joinpath("experiment.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    after = load_experiment(dest)
    records, stale = load_current_records(after, dest)
    assert "baseline__local-cli__smoke__r1" not in stale
    assert any(r.variant_id == "baseline" for r in records)
