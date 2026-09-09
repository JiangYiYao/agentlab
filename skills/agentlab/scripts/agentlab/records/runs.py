from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from agentlab.records.provenance import atomic_json, tree_digest
from agentlab.schema import Experiment
from agentlab.models import Trial
from agentlab.records.storage import archive_outputs


def with_run_repetitions(exp: Experiment, manifest: dict[str, Any] | None) -> Experiment:
    """Use the run's effective sample count while keeping current evaluation rules."""
    if manifest:
        count = (manifest.get("experiment") or {}).get("repetitions")
        if count is None:
            count = (manifest.get("overrides") or {}).get("repetitions")
        if count is not None:
            return exp.model_copy(update={"repetitions": count})
    return exp


def new_run_id(root: Path) -> str:
    now = datetime.now(timezone.utc)
    base = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}"
    ident = base
    n = 2
    while (runs_dir(root) / ident).exists():
        ident = f"{base}-{n}"
        n += 1
    return ident


def runs_dir(root: Path) -> Path:
    return root / "runs"


def latest_run_id(root: Path) -> str | None:
    marker = runs_dir(root) / "LATEST"
    if marker.is_file():
        ident = marker.read_text(encoding="utf-8").strip()
        if ident:
            return ident
    if not runs_dir(root).is_dir():
        return None
    dirs = [p.name for p in runs_dir(root).iterdir() if p.is_dir()]
    return max(dirs) if dirs else None


def load_manifest(root: Path, run_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("invalid run id")
    path = runs_dir(root) / run_id / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def planned_ids_for_run(root: Path, run_id: str | None = None) -> list[str] | None:
    ident = run_id or latest_run_id(root)
    if not ident:
        return None
    data = load_manifest(root, ident)
    if not data:
        return None
    planned = data.get("planned")
    if not isinstance(planned, list):
        return None
    return [str(x) for x in planned]


def update_manifest(root: Path, run_id: str, **fields: Any) -> Path:
    data = load_manifest(root, run_id) or {"run_id": run_id}
    data.update(fields)
    data["run_id"] = run_id
    return write_manifest(root, data)


def write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    run_id = str(payload["run_id"])
    dest = runs_dir(root) / run_id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "manifest.json"
    atomic_json(path, payload)
    (runs_dir(root) / "LATEST").write_text(run_id + "\n", encoding="utf-8")
    return path


def filter_ids(ids: Iterable[str] | None) -> set[str] | None:
    if ids is None:
        return None
    return set(ids)


def archive_trial(root: Path, run_id: str, trial_id: str, *, reused_from: str | None = None) -> Path:
    src = root / "trials" / trial_id
    dest = runs_dir(root) / run_id / "trials" / trial_id
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("meta.json", "scores.json"):
        src_file = src / name
        if src_file.is_file():
            shutil.copy2(src_file, dest / name)
    out_src = src / "outputs"
    if out_src.is_dir():
        meta = json.loads((src / "meta.json").read_text()) if (src / "meta.json").is_file() else {}
        evaluation = root / "evaluations" / run_id / "trials" / trial_id
        previous = root / "runs" / reused_from / "trials" / trial_id / "outputs" if reused_from else None
        archive_outputs(root, out_src, dest / "outputs", evaluation, meta.get("execution_id"), previous=previous)
    def relocate(value):
        if isinstance(value, str):
            return value.replace(str(src) + "/outputs/", str(dest) + "/outputs/")
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        return value
    for name in ("meta.json", "scores.json"):
        path = dest / name
        if path.is_file():
            atomic_json(path, relocate(json.loads(path.read_text())))
            if out_src.is_dir():
                shutil.copy2(path, evaluation / name)
    meta_path = dest / "meta.json"
    if meta_path.is_file() and reused_from:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                meta["reused_from"] = reused_from
                meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    return dest


def write_trial_meta(trial: Trial, extra: dict[str, Any], *, score_basis: str | None = None) -> None:
    path = trial.trial_dir() / "meta.json"
    prev: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prev = loaded
        except (OSError, json.JSONDecodeError):
            prev = {}
    meta = dict(prev)
    meta.update(
        {
            "trial_id": trial.id,
            "execution_id": trial.execution_id,
            "execution_basis": trial.execution_basis,
            "measurement_basis": trial.measurement_basis,
            "compare_basis": trial.compare_basis,
            "evaluation_events": trial.evaluation_events,
            "stage_times": trial.stage_times,
            "evidence_digest": tree_digest(trial.outputs_dir() / "evidence"),
            "sandbox": str(trial.sandbox.root) if trial.sandbox else None,
            "project_root": str(trial.sandbox.project_root) if trial.sandbox else None,
            "variant_id": trial.variant.id,
            "cell_id": trial.cell.id,
            "case_id": trial.case.id,
            "repeat": trial.repeat,
            "role": trial.variant.role,
            "contract_hash": trial.contract_hash,
            "score_basis": score_basis,
            "freeze_sha": trial.freeze_sha,
            "error_code": trial.error_code,
            "killed_reason": trial.killed_reason,
            "skipped": trial.skipped,
            "stdout": str(trial.outputs_dir() / "stdout.log"),
        }
    )
    meta.update(extra)
    if "workspace_snap" in extra:
        meta.pop("pid", None)
        meta.pop("pgid", None)
    if trial.result:
        meta["exit_code"] = trial.result.exit_code
        meta["wall_clock_s"] = trial.result.wall_clock_s
        meta["usage"] = vars(trial.result.usage)
        meta["error_code"] = trial.error_code or trial.result.error_code
        meta["execution_error"] = trial.result.error_code
    atomic_json(path, meta)


def write_trial_scores(trial: Trial) -> None:
    payload = [s.to_json() for s in trial.scores]
    path = trial.trial_dir() / "scores.json"
    if path.is_file():
        try:
            if json.loads(path.read_text()) == payload:
                return
        except (OSError, ValueError):
            pass
    atomic_json(path, payload)
