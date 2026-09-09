"""Read archived measurements without starting execution or evaluation."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable
from agentlab.schema import Experiment, fingerprint_score_basis, fingerprint_contract
from agentlab.models import Score, Usage
from agentlab.evaluation.gate import TrialRecord
from agentlab.execution.expand import expand
from agentlab.records.runs import latest_run_id, runs_dir
from agentlab.records.provenance import freeze_experiment, execution_basis, measurement_basis


def _meta_current(meta: dict[str, Any], exp: Experiment) -> bool:
    basis = fingerprint_score_basis(exp)
    if meta.get("score_basis") == basis:
        return True
    if not meta.get("score_basis") and meta.get("contract_hash") == fingerprint_contract(exp):
        return True
    return False


def load_current_records(
    exp: Experiment,
    root: Path,
    *,
    trial_ids: Iterable[str] | None = None,
    run_id: str | None = None,
    historical: bool = False,
) -> tuple[list[TrialRecord], list[str]]:
    if not historical:
        exp = freeze_experiment(exp, root)
    records: list[TrialRecord] = []
    stale: list[str] = []
    ident = run_id or latest_run_id(root)
    search = []
    if ident:
        search.append(root / "runs" / ident / "trials")
    if not ident:
        search.append(root / "trials")
    wanted = set(trial_ids) if trial_ids is not None else None
    seen: set[str] = set()
    candidates = {t.id: t for t in expand(exp, root)} if not historical else {}
    for trials_dir in search:
        if not trials_dir.is_dir():
            continue
        for meta_path in trials_dir.glob("*/meta.json"):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            tid = str(meta.get("trial_id") or meta_path.parent.name)
            if tid in seen:
                continue
            scores_path = meta_path.parent / "scores.json"
            if wanted is not None and tid not in wanted:
                continue
            current = _meta_current(meta, exp)
            if not historical and meta.get("execution_basis"):
                candidate = candidates.get(tid)
                current = candidate is not None and execution_basis(exp, candidate) == meta["execution_basis"]
                if current:
                    candidate.execution_basis = meta["execution_basis"]
                    current = all(meta.get("measurement_basis", {}).get(c.id) == measurement_basis(exp, candidate, c)
                                  for c in exp.concerns) if not meta.get("error_code") else True
            if not historical and not current:
                stale.append(tid)
                continue
            if not scores_path.is_file():
                continue
            seen.add(tid)
            raw = json.loads(scores_path.read_text(encoding="utf-8"))
            scores = {item["concern_id"]: Score.from_json(item) for item in raw}
            records.append(
                TrialRecord(
                    trial_id=tid,
                    variant_id=meta["variant_id"],
                    cell_id=meta["cell_id"],
                    case_id=meta["case_id"],
                    repeat=int(meta.get("repeat", 1)),
                    role=meta.get("role", "treatment"),
                    scores=scores,
                    skipped=bool(meta.get("skipped")),
                    execution_ok=not bool(meta.get("error_code")),
                )
            )
    return records, stale




def load_compare_results(root: Path, run_id: str | None) -> list[dict[str, Any]]:
    # An explicit historical run must never borrow mutable current results.
    bases = [runs_dir(root) / run_id / "compare"] if run_id else [root / "trials" / ".compare"]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/result.json")):
            key = path.parent.name
            if key in seen:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                data["path"] = str(path)
                out.append(data)
                seen.add(key)
    return out


def read_usage(path: Path) -> Usage:
    if not path.is_file():
        return Usage()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Usage()
    if not isinstance(data, dict):
        return Usage()
    for key in ("tokens_in", "tokens_out", "usd"):
        value = data.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            return Usage()
    tokens_in = data.get("tokens_in")
    tokens_out = data.get("tokens_out")
    usd = data.get("usd")
    return Usage(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usd=usd,
        tokens_unknown=tokens_in is None and tokens_out is None,
        usd_unknown=usd is None,
    )
