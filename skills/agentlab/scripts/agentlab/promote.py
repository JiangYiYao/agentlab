from __future__ import annotations

import json
import shutil
from pathlib import Path

from agentlab.gate import evaluate_promotion
from agentlab.runs import latest_run_id, planned_ids_for_run, load_manifest, with_run_repetitions
from agentlab.errors import ContractError
from agentlab.scheduler import load_current_records
from agentlab.schema import Experiment


def promote(
    exp: Experiment,
    root: Path,
    *,
    only_variant: str,
    force: bool = False,
    copy: bool = False,
) -> tuple[int, Path]:
    if only_variant not in {v.id for v in exp.variants if v.role == "treatment"}:
        raise ContractError("unknown_field", f"unknown treatment: {only_variant}")
    manifest = load_manifest(root, latest_run_id(root)) if latest_run_id(root) else None
    exp = with_run_repetitions(exp, manifest)
    records, stale = load_current_records(
        exp, root, trial_ids=planned_ids_for_run(root), run_id=latest_run_id(root)
    )
    promo = evaluate_promotion(exp, records, only_variants={only_variant},
                               only_cells={manifest["only_cell"]} if manifest and manifest.get("only_cell") else None,
                               only_cases={manifest["only_case"]} if manifest and manifest.get("only_case") else None)
    promo.ignored_stale = stale
    dest = root / "promotion.json"
    dest.write_text(json.dumps(promo.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    archive = root / "promotions"
    archive.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest, archive / f"{only_variant}.json")
    vp = promo.variants.get(only_variant)
    promotable = bool(vp and vp.recommend_ship)
    if not promotable and not force:
        return 1, dest
    if copy and vp and vp.recommend_ship:
        released = root / "released" / only_variant
        src = root / next(v.path for v in exp.variants if v.id == only_variant)
        if released.exists():
            shutil.rmtree(released)
        shutil.copytree(src, released)
    return 0, dest
