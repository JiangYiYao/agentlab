from __future__ import annotations

from agentlab.gate import TrialRecord, evaluate_promotion, gate_exit_code
from agentlab.models import Score
from agentlab.schema import Experiment


def _exp(**kwargs) -> Experiment:
    data = {
        "schema_version": 1,
        "id": "gate-ex",
        "name": "gate",
        "artifact": {"type": "dir", "name": "x"},
        "criteria": {"path": "criteria.md", "sha256": "ab" * 32},
        "variants": [
            {"id": "baseline", "role": "baseline", "path": "variants/baseline"},
            {"id": "treat", "role": "treatment", "path": "variants/treat", "hypothesis": {"change": "c", "bet": "b", "hurt": "h", "falsify": "f"}},
        ],
        "concerns": [
            {
                "id": "g1",
                "intent": "gold",
                "role": "gate",
                "scope": "case",
                "measure": {"type": "gold_tree", "gold_dir": "gold"},
                "pass": {"op": "==", "vs": "value", "value": True, "min_n": 1},
                "aggregate": "all_pass",
            },
            {
                "id": "g3",
                "intent": "path",
                "role": "gate",
                "scope": "case",
                "measure": {"type": "path_under", "file": "x", "json_path": "$.a", "prefix_env": "AGENTLAB_TRIAL_OUT"},
                "pass": {"op": "==", "vs": "value", "value": True},
                "aggregate": "all_pass",
            },
            {
                "id": "o1",
                "intent": "clean",
                "role": "objective",
                "scope": "case",
                "measure": {"type": "script", "command": ["true"]},
                "pass": {"op": ">", "vs": "baseline"},
                "aggregate": "mean",
            },
            {
                "id": "o2",
                "intent": "lat",
                "role": "objective",
                "measure": {"type": "cost", "quantity": "wall_clock_s"},
            },
        ],
        "matrix": {"cells": [{"id": "c1", "command": ["true"]}, {"id": "c2", "command": ["true"]}]},
        "cases": [{"id": "k1"}, {"id": "k2"}],
        "isolation": {"type": "tempdir"},
        "budget": {"max_trials": 40, "per_trial": {"wall_clock_s": 10}},
        "promotion": {"all_cells_must_pass": True},
        "repetitions": 2,
    }
    data.update(kwargs)
    return Experiment.model_validate(data)


def _rec(variant, cell, case, repeat, scores, role="treatment") -> TrialRecord:
    return TrialRecord(
        trial_id=f"{variant}__{cell}__{case}__r{repeat}",
        variant_id=variant,
        cell_id=cell,
        case_id=case,
        repeat=repeat,
        role=role,
        scores=scores,
    )


def _ok(cid: str, value=True) -> Score:
    return Score(concern_id=cid, value=value, pass_=True if isinstance(value, bool) else None, unknown=False)


def _all_ok(g1_unknown_at=None) -> list[TrialRecord]:
    recs = []
    for variant, role in (("baseline", "baseline"), ("treat", "treatment")):
        for cell in ("c1", "c2"):
            for case in ("k1", "k2"):
                for r in (1, 2):
                    scores = {
                        "g1": _ok("g1"),
                        "g3": _ok("g3"),
                        "o1": _ok("o1", 0.7 if variant == "baseline" else 0.9),
                        "o2": _ok("o2", 10.0),
                        "__isolation_leak__": _ok("__isolation_leak__"),
                        "__wrong_skill_tree__": _ok("__wrong_skill_tree__"),
                    }
                    if g1_unknown_at and (variant, cell, case, r) == g1_unknown_at:
                        scores["g1"] = Score(concern_id="g1", unknown=True, pass_=False)
                    recs.append(_rec(variant, cell, case, r, scores, role))
    return recs


def test_g1_unknown_blocks_promotion() -> None:
    exp = _exp()
    recs = _all_ok(g1_unknown_at=("treat", "c1", "k2", 2))
    promo = evaluate_promotion(exp, recs)
    assert promo.variants["treat"].promotable is False
    assert promo.variants["treat"].recommend_ship is False
    assert gate_exit_code(promo, gate=True, budget_incomplete=False, zero_trials=False, all_skipped=False) == 1


def test_variant_a_named_cells_ignores_dirty_c1() -> None:
    exp = _exp(promotion={"all_cells_must_pass": False}, matrix={
        "cells": [{"id": "c1", "command": ["true"]}, {"id": "c2", "command": ["true"]}],
        "cell_rule": {"type": "named_cells", "require": ["c2"]},
    })
    recs = _all_ok(g1_unknown_at=("treat", "c1", "k2", 2))
    promo = evaluate_promotion(exp, recs)
    assert promo.variants["treat"].promotable is True


def test_variant_b_only_cell_c2() -> None:
    exp = _exp()
    recs = _all_ok(g1_unknown_at=("treat", "c1", "k2", 2))
    promo = evaluate_promotion(exp, recs, only_cells={"c2"})
    assert promo.variants["treat"].promotable is True
    assert gate_exit_code(promo, gate=True, budget_incomplete=False, zero_trials=False, all_skipped=False) == 0


def test_variant_c_zero_treatment() -> None:
    exp = _exp()
    recs = [r for r in _all_ok() if r.role == "baseline"]
    promo = evaluate_promotion(exp, recs, only_variants=set())
    # only_variants=set() means intersection empty
    promo = evaluate_promotion(exp, recs, only_variants={"none"})
    assert promo.variants == {}
    assert promo.system_ok is True
    assert gate_exit_code(promo, gate=True, budget_incomplete=False, zero_trials=False, all_skipped=False) == 0


def test_opt_in_treatment_skipped_unless_selected() -> None:
    exp = _exp()
    dirty = exp.variants[-1].model_copy(update={"id": "fake-dirty", "opt_in": True, "path": "variants/fake-dirty"})
    exp = exp.model_copy(update={"variants": [*exp.variants, dirty]})
    recs = _all_ok()
    recs.append(
        _rec(
            "fake-dirty",
            "c1",
            "k1",
            1,
            {
                "g1": Score(concern_id="g1", unknown=True, pass_=False),
                "g3": _ok("g3"),
                "__isolation_leak__": _ok("__isolation_leak__"),
                "__wrong_skill_tree__": _ok("__wrong_skill_tree__"),
            },
        )
    )
    default = evaluate_promotion(exp, recs)
    assert "fake-dirty" not in default.variants
    assert default.variants["treat"].promotable is True
    selected = evaluate_promotion(exp, recs, only_variants={"fake-dirty"})
    assert selected.variants["fake-dirty"].promotable is False


def test_variant_d_min_n() -> None:
    concerns = [
        {
            "id": "g1",
            "intent": "gold",
            "role": "gate",
            "scope": "case",
            "measure": {"type": "gold_tree", "gold_dir": "gold"},
            "pass": {"op": "==", "vs": "value", "value": True, "min_n": 3},
            "aggregate": "all_pass",
        }
    ]
    exp = _exp(concerns=concerns, repetitions=2)
    recs = _all_ok()
    promo = evaluate_promotion(exp, recs)
    assert promo.variants["treat"].promotable is False
