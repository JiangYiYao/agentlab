from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentlab.errors import ContractError
from agentlab.schema import fingerprint_contract, fingerprint_score_basis
from agentlab.validate import load_experiment, load_raw, parse_experiment, validate_experiment, write_criteria_hash
from tests.helpers import make_min_exp


def test_load_minimal_fixture(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    exp = load_experiment(dest)
    assert exp.id == "fixture-min"
    assert exp.artifact.type == "dir"
    assert exp.isolation.type == "tempdir"
    assert exp.isolation.subdir == "."
    assert exp.isolation.inherit_host_identity is True
    assert {v.id for v in exp.variants} == {"baseline", "treatment"}
    warnings = validate_experiment(exp, dest)
    assert warnings == []
    digest = fingerprint_contract(exp)
    assert digest.startswith("sha256:")
    assert len(digest) == 71


def test_score_basis_stable_when_adding_treatment(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    exp = load_experiment(dest)
    before = fingerprint_score_basis(exp)
    raw = load_raw(dest)
    raw["variants"].append(
        {
            "id": "news-cap",
            "role": "treatment",
            "path": "variants/treatment",
            "parent": "baseline",
            "created_by": "manual",
            "hypothesis": {
                "change": "cap news",
                "bet": "faster",
                "hurt": "miss",
                "falsify": "slower",
            },
        }
    )
    dest.joinpath("experiment.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    after = load_experiment(dest)
    assert fingerprint_score_basis(after) == before
    assert fingerprint_contract(after) != fingerprint_contract(exp)


def test_fingerprint_includes_criteria_sha256(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    exp = load_experiment(dest)
    original = fingerprint_contract(exp)
    exp.criteria.sha256 = "0" * 64
    assert fingerprint_contract(exp) != original


def test_unknown_field_rejected(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["harness"] = "claude"
    with pytest.raises(ContractError) as ei:
        parse_experiment(data)
    assert ei.value.code == "unknown_field"


def test_skill_install_rejected(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["skill_install"] = True
    with pytest.raises(ContractError) as ei:
        parse_experiment(data)
    assert ei.value.code == "unknown_field"


def test_unsupported_artifact_type(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["artifact"]["type"] = "skill"
    with pytest.raises(ContractError) as ei:
        parse_experiment(data)
    assert ei.value.code == "unsupported_artifact_type"


def test_defaults_isolation(tmp_path: Path) -> None:
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["isolation"] = {"type": "tempdir"}
    exp = parse_experiment(data)
    assert exp.isolation.subdir == "."
    assert exp.isolation.inherit_host_identity is True


def test_write_criteria_hash_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "exp"
    dest.mkdir()
    (dest / "criteria.md").write_text("hello\n", encoding="utf-8")
    src = make_min_exp(tmp_path / "src")
    raw = yaml.safe_load((src / "experiment.yaml").read_text(encoding="utf-8"))
    raw["criteria"] = {"path": "criteria.md"}
    (dest / "experiment.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    for name in ("variants/baseline", "variants/treatment"):
        d = dest / name
        d.mkdir(parents=True)
        (d / "note.txt").write_text("x\n", encoding="utf-8")
    exp = load_experiment(dest)
    digest = write_criteria_hash(dest, exp, raw)
    assert digest == __import__("hashlib").sha256(b"hello\n").hexdigest()
    again = load_experiment(dest)
    assert again.criteria.sha256 == digest
