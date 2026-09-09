from __future__ import annotations

import json
import subprocess
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentlab.models import Score, Trial
from agentlab.schema import Concern, Experiment
from agentlab.templates import resolve_argv, expand_templates
from agentlab.evaluation.process import run_process
from agentlab.evaluation.evidence import file_digest
from agentlab.records.provenance import atomic_json


def run_script_measure(
    trial: Trial,
    concern: Concern,
    exp: Experiment,
    ctx: dict[str, str],
    env: dict[str, str],
    timeout_s: int,
) -> Score:
    view = trial.outputs_dir() / "evaluators" / concern.id
    if view.exists():
        shutil.rmtree(view)
    view.mkdir(parents=True)
    started = time.time()
    record = {"timeout_s": timeout_s, "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
              "exit_code": None}
    try:
        score = _execute_script(trial, concern, ctx, env, timeout_s, view, record)
    except Exception as exc:
        score = Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": str(exc)})
    finally:
        record["wall_clock_s"] = time.time() - started
        atomic_json(view / "execution.json", record)
    record["error"] = score.evidence.get("error")
    atomic_json(view / "execution.json", record)
    atomic_json(view / "result.json", score.to_json())
    return score


def _execute_script(trial, concern, ctx, env, timeout_s, view, record) -> Score:
    measure = concern.measure
    if not measure.command:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": "missing command"})
    cwd = _measure_cwd(trial, measure.cwd)
    argv = resolve_argv(list(measure.command), trial.experiment_root, ctx)
    record.update(command=argv, cwd=str(cwd))
    json_result = measure.result == "json" or (measure.result is None and bool(measure.output_json or measure.value_path))
    paths = _result_paths(trial, measure.output_json, ctx) if json_result else []
    before = {path: _file_identity(path) for path in paths}
    try:
        eval_env = dict(env)
        eval_env.update({k: expand_templates(v, ctx) for k, v in (measure.env or {}).items()})
        proc = run_process(argv, cwd, eval_env, timeout_s, log_dir=view)
        record["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": "script timeout"})
    except Exception as exc:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": str(exc)})
    if not json_result:
        return Score(concern_id=concern.id, value=proc.returncode == 0,
                     evidence={"exit_code": proc.returncode, "stderr": proc.stderr[-500:].decode(errors="replace")})
    if proc.returncode != 0:
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error": "script nonzero", "stderr": proc.stderr[-500:].decode(errors="replace")},
        )
    try:
        manifest = trial.outputs_dir() / "evidence" / "manifest.json"
        execution_files = json.loads(manifest.read_text()).get("execution_files", {}) if manifest.is_file() else {}
        out_path = None
        for path in paths:
            after = _file_identity(path)
            if after is None:
                continue
            relative = path.relative_to(trial.outputs_dir()).as_posix() if path.is_relative_to(trial.outputs_dir()) else None
            if after != before[path] or (relative in execution_files and file_digest(path) == execution_files[relative]):
                out_path = path
                break
        if out_path is None:
            return Score(concern_id=concern.id, unknown=True, pass_=False,
                         evidence={"error": "missing or stale JSON result: evaluator must write a fresh result or read an unchanged execution output",
                                   "paths": [str(path) for path in paths]})
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        atomic_json(view / "output.json", payload)
        value = _json_path(payload, measure.value_path or "$.score")
    except Exception as exc:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": str(exc)})
    return Score(
        concern_id=concern.id,
        value=value,
        unknown=False,
        evidence={"paths": [str(out_path)]},
        soft=concern.soft,
    )


def _result_paths(trial: Trial, spec: str | None, ctx: dict[str, str]) -> list[Path]:
    value = expand_templates(spec or "outputs/eval/out.json", ctx)
    primary = Path(value) if Path(value).is_absolute() else trial.trial_dir() / value
    return list(dict.fromkeys([primary, trial.outputs_dir() / Path(value).name,
                              trial.outputs_dir() / value.replace("outputs/", "", 1)]))


def _file_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) if path.is_file() else None
    except FileNotFoundError:
        return None


def _measure_cwd(trial: Trial, cwd: str | None) -> Path:
    if cwd == "sandbox" and trial.sandbox is not None:
        return trial.sandbox.project_root
    if cwd == "trial":
        return trial.trial_dir()
    return trial.experiment_root


def _json_path(obj: Any, path: str) -> Any:
    cur = obj
    spec = path[1:] if path.startswith("$") else path
    if spec.startswith("."):
        spec = spec[1:]
    if not spec:
        return cur
    for part in spec.split("."):
        if isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur
