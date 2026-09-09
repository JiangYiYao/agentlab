from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agentlab.models import Score, Trial
from agentlab.schema import Concern, Experiment
from agentlab.templates import resolve_argv, expand_templates
from agentlab.runner.evaluation import run_process


def run_script_measure(
    trial: Trial,
    concern: Concern,
    exp: Experiment,
    ctx: dict[str, str],
    env: dict[str, str],
    timeout_s: int,
) -> Score:
    measure = concern.measure
    if not measure.command:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": "missing command"})
    cwd = _measure_cwd(trial, measure.cwd)
    argv = resolve_argv(list(measure.command), trial.experiment_root, ctx)
    try:
        eval_env = dict(env)
        eval_env.update({k: expand_templates(v, ctx) for k, v in (measure.env or {}).items()})
        proc = run_process(argv, cwd, eval_env, timeout_s)
    except subprocess.TimeoutExpired:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": "script timeout"})
    except Exception as exc:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": str(exc)})
    json_result = measure.result == "json" or (measure.result is None and bool(measure.output_json or measure.value_path))
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
    out_rel = expand_templates(measure.output_json or "outputs/eval/out.json", ctx)
    out_path = trial.trial_dir() / out_rel if not Path(out_rel).is_absolute() else Path(out_rel)
    # also accept relative to trial outputs
    if not out_path.is_file():
        alt = trial.outputs_dir() / Path(out_rel).name
        if alt.is_file():
            out_path = alt
        else:
            # scripts often write relative to cwd=experiment but path is outputs/eval under trial
            trial_out = trial.outputs_dir() / out_rel.replace("outputs/", "", 1)
            if trial_out.is_file():
                out_path = trial_out
    if not out_path.is_file():
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": f"missing {out_rel}"})
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
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
