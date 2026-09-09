from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentlab.adapters.evaluator.score import SYSTEM_GATES
from agentlab.models import Score
from agentlab.schema import Concern, Experiment


@dataclass
class VariantPromotion:
    promotable: bool
    recommend_ship: bool
    cell_pass: dict[str, bool]
    failures: list[dict[str, Any]] = field(default_factory=list)
    objectives: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Promotion:
    variants: dict[str, VariantPromotion]
    system_ok: bool
    empty_required: bool = False
    ignored_stale: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "system_ok": self.system_ok,
            "empty_required": self.empty_required,
            "ignored_stale": self.ignored_stale,
            "variants": {
                vid: {
                    "promotable": vp.promotable,
                    "recommend_ship": vp.recommend_ship,
                    "cell_pass": vp.cell_pass,
                    "failures": vp.failures,
                    "objectives": vp.objectives,
                }
                for vid, vp in self.variants.items()
            },
        }


def compare_op(score_value: Any, op: str, rhs: Any, margin: float = 0.0) -> bool:
    if isinstance(score_value, bool) or isinstance(rhs, bool):
        if op not in {"==", "!="}:
            return False
        return (score_value == rhs) if op == "==" else (score_value != rhs)
    try:
        left = float(score_value)
        right = float(rhs)
    except (TypeError, ValueError):
        return False
    if op == ">":
        return left > right + margin
    if op == ">=":
        return left >= right + margin
    if op == "<":
        return left < right - margin
    if op == "<=":
        return left <= right - margin
    if op == "==":
        return abs(left - right) <= margin
    if op == "!=":
        return abs(left - right) > margin
    return False


def compare(score: Score, rule, baseline: Score | None, *, is_baseline: bool) -> bool | None:
    if score.unknown:
        return False
    if rule.vs == "baseline":
        if is_baseline:
            return None
        if baseline is None or baseline.unknown:
            return False
        rhs = baseline.value
    else:
        rhs = rule.value
    return compare_op(score.value, rule.op, rhs, rule.margin or 0.0)


@dataclass
class TrialRecord:
    trial_id: str
    variant_id: str
    cell_id: str
    case_id: str
    repeat: int
    role: str
    scores: dict[str, Score]
    skipped: bool = False
    execution_ok: bool = True


def evaluate_promotion(
    exp: Experiment,
    records: list[TrialRecord],
    *,
    only_variants: set[str] | None = None,
    only_cells: set[str] | None = None,
    only_cases: set[str] | None = None,
) -> Promotion:
    treatments = [v for v in exp.variants if v.role == "treatment"]
    if only_variants is None:
        V = [v for v in treatments if not v.opt_in]
    else:
        V = [v for v in treatments if v.id in only_variants]
    C = [c for c in exp.matrix.cells if only_cells is None or c.id in only_cells]
    K = [k for k in exp.cases if only_cases is None or k.id in only_cases]
    if exp.promotion.all_cells_must_pass:
        required = [c.id for c in C]
    else:
        require = (exp.matrix.cell_rule or {}).get("require") or []
        required = [cid for cid in require if cid in {c.id for c in C}]
    if not required:
        return Promotion(variants={}, system_ok=False, empty_required=True)

    selected_ids = {v.id for v in V} | {_baseline_id(exp)}
    records = [r for r in records if r.cell_id in {c.id for c in C} and r.case_id in {k.id for k in K} and r.variant_id in selected_ids]
    system_ok = all_system_gates_ok(records, C, K)
    if not V:
        present = {(r.cell_id, r.case_id, r.repeat) for r in records if r.variant_id == _baseline_id(exp) and not r.skipped and r.execution_ok}
        system_ok = system_ok and all((cid, case.id, repeat) in present for cid in required for case in K for repeat in range(1, exp.repetitions + 1))
        for concern in exp.concerns:
            if concern.role != "gate" and not (concern.role == "objective" and concern.pass_):
                continue
            if concern.pass_ and concern.pass_.vs == "baseline":
                continue
            for cell_id in required:
                for case in K if concern.scope == "case" else [None]:
                    case_id = case.id if case else None
                    if concern.role == "gate":
                        sc = aggregate_all_pass(records, _baseline_id(exp), cell_id, case_id, concern)
                        good = sc.pass_ is True
                    else:
                        sc = aggregate_score(records, _baseline_id(exp), cell_id, case_id, concern)
                        good = compare(sc, concern.pass_, None, is_baseline=True) is True
                    system_ok = system_ok and good and sc.n >= (concern.pass_.min_n if concern.pass_ else 1)
        return Promotion(variants={}, system_ok=system_ok)

    out: dict[str, VariantPromotion] = {}
    for variant in V:
        cell_pass: dict[str, bool] = {}
        failures: list[dict[str, Any]] = []
        for cell in C:
            present = {(r.case_id, r.repeat) for r in records if r.variant_id == variant.id and r.cell_id == cell.id and not r.skipped and r.execution_ok}
            ok = all((case.id, repeat) in present for case in K for repeat in range(1, exp.repetitions + 1))
            if not ok:
                failures.append({"cell": cell.id, "reason": "missing_or_failed_trials"})
            for gate in [c for c in exp.concerns if c.role == "gate"]:
                units = K if gate.scope == "case" else [None]
                for case in units:
                    sc = aggregate_all_pass(records, variant.id, cell.id, case.id if case else None, gate)
                    min_n = gate.pass_.min_n if gate.pass_ else 1
                    if sc.n < (min_n or 1):
                        ok = False
                        failures.append({"concern": gate.id, "cell": cell.id, "reason": "min_n"})
                    else:
                        if sc.unknown or sc.pass_ is not True:
                            ok = False
                            failures.append({"concern": gate.id, "cell": cell.id, "reason": "fail"})
            for gid in SYSTEM_GATES:
                # system: any unknown/false on this cell
                if not _system_cell_ok(records, variant.id, cell.id, gid):
                    ok = False
                    failures.append({"concern": gid, "cell": cell.id, "reason": "system"})
            cell_pass[cell.id] = ok
        promotable = all(cell_pass.get(cid, False) for cid in required) and system_ok
        recs = []
        obj_ok = True
        for obj in [c for c in exp.concerns if c.role == "objective"]:
            cells = _objective_cells(obj, variant.id, records, required, K, exp)
            if obj.pass_ is None:
                recs.append(
                    {
                        "id": obj.id,
                        "status": "observed_only",
                        "aggregate": obj.aggregate or "mean",
                        "cells": cells,
                    }
                )
                continue
            good = bool(cells) and all(c.get("ok") for c in cells)
            recs.append(
                {
                    "id": obj.id,
                    "status": "ok" if good else "not_ok",
                    "aggregate": obj.aggregate or "mean",
                    "cells": cells,
                }
            )
            obj_ok = obj_ok and good
        out[variant.id] = VariantPromotion(
            promotable=promotable,
            recommend_ship=promotable and obj_ok,
            cell_pass=cell_pass,
            failures=failures,
            objectives=recs,
        )
    return Promotion(variants=out, system_ok=system_ok)


def _baseline_id(exp: Experiment) -> str:
    for v in exp.variants:
        if v.role == "baseline":
            return v.id
    return "baseline"


def _count_unknown(
    records: list[TrialRecord], variant_id: str, cell_id: str, case_id: str | None, concern_id: str
) -> int:
    n = 0
    for rec in _match(records, variant_id, cell_id, case_id):
        if rec.skipped:
            continue
        sc = rec.scores.get(concern_id)
        if sc is None or sc.unknown:
            n += 1
    return n


def _objective_cells(
    obj: Concern,
    variant_id: str,
    records: list[TrialRecord],
    required: list[str],
    cases,
    exp: Experiment,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cell_id in required:
        units = cases if obj.scope == "case" else [None]
        for case in units:
            case_id = case.id if case else None
            sc = aggregate_score(records, variant_id, cell_id, case_id, obj)
            item: dict[str, Any] = {
                "cell": cell_id,
                "n": sc.n,
                "unknown_n": _count_unknown(records, variant_id, cell_id, case_id, obj.id),
                "value": sc.value,
                "unknown": sc.unknown,
            }
            if case_id is not None:
                item["case"] = case_id
            base = None
            if obj.pass_ and obj.pass_.vs == "baseline":
                base = aggregate_score(records, _baseline_id(exp), cell_id, case_id, obj)
                item["baseline"] = base.value
                try:
                    if sc.value is not None and base.value is not None:
                        item["delta"] = round(float(sc.value) - float(base.value), 12)
                except (TypeError, ValueError):
                    pass
            if obj.pass_ is None:
                item["ok"] = None
            elif sc.n < (obj.pass_.min_n or 1):
                item["ok"] = False
            else:
                passed = compare(sc, obj.pass_, base, is_baseline=False)
                item["ok"] = passed is not False
            out.append(item)
    return out


def aggregate_all_pass(
    records: list[TrialRecord],
    variant_id: str,
    cell_id: str,
    case_id: str | None,
    concern: Concern,
) -> Score:
    matched = _match(records, variant_id, cell_id, case_id)
    n = 0
    unknown = not matched
    passed = bool(matched)
    for rec in matched:
        if rec.skipped:
            unknown = True
            passed = False
            continue
        n += 1
        sc = rec.scores.get(concern.id)
        if sc is None or sc.unknown:
            unknown = True
            passed = False
            continue
        if concern.pass_:
            baseline = next((r.scores.get(concern.id) for r in records
                             if r.role == "baseline" and r.cell_id == rec.cell_id
                             and r.case_id == rec.case_id and r.repeat == rec.repeat and not r.skipped), None)
            passed = passed and compare(sc, concern.pass_, baseline, is_baseline=rec.role == "baseline") is not False
        else:
            passed = passed and sc.pass_ is not False and sc.value is not False
    return Score(concern_id=concern.id, value=passed, unknown=unknown, pass_=passed and not unknown, n=n)


def aggregate_score(
    records: list[TrialRecord],
    variant_id: str,
    cell_id: str,
    case_id: str | None,
    concern: Concern,
) -> Score:
    kind = concern.aggregate or "mean"
    if kind == "all_pass":
        return aggregate_all_pass(records, variant_id, cell_id, case_id, concern)
    matched = _match(records, variant_id, cell_id, case_id)
    vals: list[float] = []
    unknown = False
    for rec in matched:
        if rec.skipped:
            continue
        sc = rec.scores.get(concern.id)
        if sc is None or sc.unknown or sc.value is None:
            unknown = True
            continue
        try:
            vals.append(float(sc.value))
        except (TypeError, ValueError):
            unknown = True
    if not vals:
        return Score(concern_id=concern.id, value=None, unknown=True, n=0)
    if kind == "min":
        value: float | None = min(vals)
    elif kind == "max":
        value = max(vals)
    elif kind == "median":
        ordered = sorted(vals)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            value = ordered[mid]
        else:
            value = (ordered[mid - 1] + ordered[mid]) / 2
    else:
        value = sum(vals) / len(vals)
    return Score(concern_id=concern.id, value=value, unknown=unknown, n=len(vals))


def aggregate_mean(
    records: list[TrialRecord],
    variant_id: str,
    cell_id: str,
    case_id: str | None,
    concern: Concern,
) -> Score:
    matched = _match(records, variant_id, cell_id, case_id)
    vals: list[float] = []
    unknown = False
    for rec in matched:
        if rec.skipped:
            continue
        sc = rec.scores.get(concern.id)
        if sc is None or sc.unknown or sc.value is None:
            unknown = True
            continue
        try:
            vals.append(float(sc.value))
        except (TypeError, ValueError):
            unknown = True
    mean = sum(vals) / len(vals) if vals else None
    return Score(concern_id=concern.id, value=mean, unknown=unknown or mean is None, n=len(vals))


def _match(records: list[TrialRecord], variant_id: str, cell_id: str, case_id: str | None) -> list[TrialRecord]:
    out = []
    for rec in records:
        if rec.variant_id != variant_id or rec.cell_id != cell_id:
            continue
        if case_id is not None and rec.case_id != case_id:
            continue
        out.append(rec)
    return out


def _system_cell_ok(records: list[TrialRecord], variant_id: str, cell_id: str, gid: str) -> bool:
    matched = _match(records, variant_id, cell_id, None)
    if not matched:
        return False
    for rec in matched:
        sc = rec.scores.get(gid)
        if sc is None or sc.unknown or sc.pass_ is False or sc.value is False:
            return False
    return True


def all_system_gates_ok(records: list[TrialRecord], cells, cases) -> bool:
    if not records:
        return False
    for rec in records:
        if rec.skipped or not rec.execution_ok:
            return False
        for gid in SYSTEM_GATES:
            sc = rec.scores.get(gid)
            if sc is None or sc.unknown or sc.pass_ is False or sc.value is False:
                return False
    return True


def gate_exit_code(
    promo: Promotion,
    *,
    gate: bool,
    budget_incomplete: bool,
    zero_trials: bool,
    all_skipped: bool,
    env_incomplete: bool = False,
) -> int:
    if budget_incomplete or env_incomplete:
        return 3
    if not gate:
        return 0
    if zero_trials or promo.empty_required:
        return 1
    if all_skipped:
        return 1
    if not promo.variants:
        return 0 if promo.system_ok else 1
    if any(not vp.promotable or not vp.recommend_ship for vp in promo.variants.values()):
        return 1
    return 0
