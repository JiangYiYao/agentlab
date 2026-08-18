from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from agentlab.gate import TrialRecord
from agentlab.schema import Experiment


def concern_stats(exp: Experiment, records: list[TrialRecord]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for rec in records:
        if rec.skipped:
            continue
        for cid, score in rec.scores.items():
            if score.unknown or score.value is None:
                continue
            try:
                groups[(cid, rec.cell_id, rec.variant_id)].append(float(score.value))
            except (TypeError, ValueError):
                continue
    out = []
    for (cid, cell, variant), vals in sorted(groups.items()):
        item = {
            "concern": cid,
            "cell": cell,
            "variant": variant,
            "n": len(vals),
            "mean": sum(vals) / len(vals) if vals else None,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
        }
        if len(vals) < 3:
            item["warning"] = "n 不够，区间不可靠"
        else:
            mean = item["mean"] or 0
            var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
            se = math.sqrt(var / len(vals))
            item["ci95"] = [mean - 1.96 * se, mean + 1.96 * se]
        out.append(item)
    return out


def paired_deltas(exp: Experiment, records: list[TrialRecord]) -> list[dict[str, Any]]:
    baseline = next((v.id for v in exp.variants if v.role == "baseline"), None)
    if not baseline:
        return []
    index: dict[tuple[str, str, str, int, str], float] = {}
    for rec in records:
        for cid, score in rec.scores.items():
            if score.unknown or score.value is None:
                continue
            try:
                index[(cid, rec.cell_id, rec.case_id, rec.repeat, rec.variant_id)] = float(score.value)
            except (TypeError, ValueError):
                continue
    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for (cid, cell, case, repeat, variant), value in index.items():
        if variant == baseline:
            continue
        key = (cid, cell, case, repeat, baseline)
        if key not in index:
            continue
        acc[(cid, cell, variant)].append(value - index[key])
    out = []
    for (cid, cell, variant), deltas in sorted(acc.items()):
        out.append(
            {
                "concern": cid,
                "cell": cell,
                "variant": variant,
                "n": len(deltas),
                "delta_mean": sum(deltas) / len(deltas) if deltas else None,
            }
        )
    return out


def preview_cost(exp: Experiment) -> dict[str, Any]:
    n = len(exp.variants) * len(exp.matrix.cells) * len(exp.cases) * exp.repetitions
    usd = None
    if exp.budget.usd is not None:
        usd = exp.budget.usd
    elif exp.budget.per_trial.usd is not None:
        usd = n * exp.budget.per_trial.usd
    disk = None
    # Case 2 freeze × parallel rough estimate
    if any(c.replay for c in exp.cases):
        disk = f"~{max(1, exp.budget.max_parallel) * 8}MB scutio-home (freeze copied per trial)"
    return {"trials": n, "budget_usd_cap": usd if usd is not None else "n/a", "disk": disk}
