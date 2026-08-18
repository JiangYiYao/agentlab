from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from agentlab.errors import ContractError
from agentlab.secrets_scan import scan_experiment_secrets
from agentlab.validate import parse_experiment, validate_experiment
from tests.helpers import make_min_exp


def _src(tmp_path: Path) -> Path:
    return make_min_exp(tmp_path / "src")


def _data(tmp_path: Path) -> dict:
    return yaml.safe_load((_src(tmp_path) / "experiment.yaml").read_text(encoding="utf-8"))


def _exp_dir(tmp_path: Path, data: dict, *, copy_variants: bool = True) -> Path:
    dest = tmp_path / "exp"
    dest.mkdir()
    src = _src(tmp_path)
    if copy_variants:
        shutil.copytree(src / "variants", dest / "variants")
        shutil.copytree(src / "cases", dest / "cases")
        shutil.copy2(src / "criteria.md", dest / "criteria.md")
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return dest


def _validate(tmp_path: Path, data: dict) -> None:
    dest = _exp_dir(tmp_path, data)
    exp = parse_experiment(data)
    validate_experiment(exp, dest)


def test_missing_criteria_hash(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["criteria"] = {"path": "criteria.md"}
    dest = _exp_dir(tmp_path, data)
    exp = parse_experiment(data)
    with pytest.raises(ContractError) as ei:
        validate_experiment(exp, dest)
    assert ei.value.code == "missing_criteria"


def test_criteria_hash_mismatch(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["criteria"]["sha256"] = "ab" * 32
    dest = _exp_dir(tmp_path, data)
    exp = parse_experiment(data)
    with pytest.raises(ContractError) as ei:
        validate_experiment(exp, dest)
    assert ei.value.code == "criteria_hash_mismatch"


def test_missing_judge_command(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["concerns"] = [
        {
            "id": "rubric",
            "intent": "llm",
            "role": "metric",
            "soft": True,
            "measure": {"type": "llm_rubric", "source": "criteria.md#rubric"},
        }
    ]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "missing_judge_command"


def test_need_exactly_one_baseline(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["variants"][0]["role"] = "treatment"
    data["variants"][0]["hypothesis"] = {
        "change": "x",
        "bet": "y",
        "hurt": "z",
        "falsify": "w",
    }
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "need_exactly_one_baseline"


def test_too_many_treatments(tmp_path: Path) -> None:
    data = _data(tmp_path)
    hypo = {"change": "x", "bet": "y", "hurt": "z", "falsify": "w"}
    extra = []
    for i in range(4):
        ident = f"treat-{i}"
        extra.append(
            {
                "id": ident,
                "role": "treatment",
                "path": "variants/treatment",
                "hypothesis": hypo,
            }
        )
    data["variants"] = [data["variants"][0], *extra]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "too_many_treatments"


def test_too_many_concerns(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["concerns"] = [
        {
            "id": f"c-{i}",
            "intent": "x",
            "role": "metric",
            "measure": {"type": "script", "command": ["true"]},
        }
        for i in range(9)
    ]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "too_many_concerns"


def test_treatment_missing_hypothesis(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["variants"][1]["hypothesis"] = None
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "treatment_missing_hypothesis"


def test_baseline_must_not_hypothesize(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["variants"][0]["hypothesis"] = {
        "change": "x",
        "bet": "y",
        "hurt": "z",
        "falsify": "w",
    }
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "baseline_must_not_hypothesize"


def test_budget_reserve_required(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["budget"]["usd"] = 10
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "budget_reserve_required"


def test_unknown_cell_in_rule(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["promotion"]["all_cells_must_pass"] = False
    data["matrix"]["cell_rule"] = {"type": "named_cells", "require": ["missing-cell"]}
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "unknown_cell_in_rule"


def test_unknown_concern_in_accept(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["promotion"]["accept_soft_gates"] = ["not-a-concern"]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "unknown_concern_in_accept"


def test_reserved_env_key_on_cell(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["matrix"]["cells"][0]["env"] = {"SCUTIO_HOME": "/tmp/x"}
    with pytest.raises(ContractError) as ei:
        parse_experiment(data)
    assert ei.value.code == "reserved_env_key"


def test_reserved_env_key_on_isolation_identity(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["isolation"]["env_inject"] = {"HOME": "/tmp/home"}
    with pytest.raises(ContractError) as ei:
        parse_experiment(data)
    assert ei.value.code == "reserved_env_key"


def test_recipe_env_not_allowed(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["recipes"] = {"local": {"command": ["true"], "env": {"SCUTIO_HOME": "x"}}}
    with pytest.raises(ContractError) as ei:
        parse_experiment(data)
    assert ei.value.code == "recipe_env_not_allowed"


def test_case2_env_inject_accepted(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["isolation"] = {
        "type": "homedir",
        "inherit_host_identity": True,
        "env_inject": {
            "SCUTIO_HOME": "${trial_out}/scutio-home",
            "SCUTIO_PYTHON": "${trial_out}/bin/scutio-python-replay",
            "SCUTIO_TOOLKIT_SCRIPTS": "${host.scutio_toolkit_scripts}",
            "AGENTLAB_REPLAY_NOW": "${case.replay.clock}",
            "AGENTLAB_REPLAY_LOCK": "${trial_out}/replay.lock.json",
            "AGENTLAB_TRIAL_OUT": "${trial_out}",
            "AGENTLAB_EXPERIMENT_ROOT": "${experiment_root}",
            "AGENTLAB_PROJECT_ROOT": "${project_root}",
            "AGENTLAB_PROGRAM_ROOT": "${program_root}",
            "AGENTLAB_CASE_DIR": "${case.path}",
        },
    }
    dest = _exp_dir(tmp_path, data)
    exp = parse_experiment(data)
    warnings = validate_experiment(exp, dest)
    assert warnings == []


def test_model_unbound(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["matrix"]["cells"][0]["command"] = ["true", "--model", "${cell.model}"]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "model_unbound"


def test_bin_not_on_path(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["matrix"]["cells"][0]["command"] = ["definitely-not-a-bin-agentlab"]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "bin_not_on_path"


def test_worktree_missing_repo(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["isolation"] = {"type": "git-worktree"}
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "worktree_missing_repo"


def test_gate_missing_scope(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["concerns"] = [
        {
            "id": "gold",
            "intent": "gate",
            "role": "gate",
            "measure": {"type": "must_list", "keep": "keep.txt", "mode": "paths_or_substrings"},
            "pass": {"op": "==", "vs": "value", "value": True},
            "aggregate": "all_pass",
        }
    ]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "gate_missing_scope"


def test_llm_cannot_be_gate(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["judge"] = {"command": ["true"]}
    data["concerns"] = [
        {
            "id": "rubric",
            "intent": "llm",
            "role": "gate",
            "scope": "case",
            "measure": {"type": "llm_rubric", "source": "criteria.md#rubric"},
            "pass": {"op": ">=", "vs": "value", "value": 0.8},
            "aggregate": "all_pass",
        }
    ]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "llm_cannot_be_gate"


def test_no_average_cell_pass(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["promotion"]["all_cells_must_pass"] = False
    data["matrix"]["cell_rule"] = None
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "no_average_cell_pass"


def test_secrets_in_experiment(tmp_path: Path) -> None:
    dest = _exp_dir(tmp_path, _data(tmp_path))
    (dest / "secrets.env").write_text("TOKEN=sk-abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
    with pytest.raises(ContractError) as ei:
        scan_experiment_secrets(dest)
    assert ei.value.code == "secrets_in_experiment"


def test_artifact_missing_dir(tmp_path: Path) -> None:
    data = _data(tmp_path)
    dest = _exp_dir(tmp_path, data)
    shutil.rmtree(dest / "variants" / "treatment")
    exp = parse_experiment(data)
    with pytest.raises(ContractError) as ei:
        validate_experiment(exp, dest)
    assert ei.value.code == "artifact_missing_dir"


def test_unknown_template_var(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data["matrix"]["cells"][0]["command"] = ["true", "${not_a_var}"]
    with pytest.raises(ContractError) as ei:
        _validate(tmp_path, data)
    assert ei.value.code == "unknown_template_var"
