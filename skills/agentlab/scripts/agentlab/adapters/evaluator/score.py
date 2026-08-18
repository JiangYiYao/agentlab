from __future__ import annotations

from agentlab.adapters.evaluator.builtin import builtin_evaluate
from agentlab.adapters.evaluator.script import run_script_measure
from agentlab.judge import spawn_judge
from agentlab.models import Score, Trial
from agentlab.schema import Experiment

SYSTEM_GATES = ["__isolation_leak__", "__wrong_skill_tree__"]


def fail_closed_for_gates(trial: Trial, exp: Experiment, *, reason: str) -> list[Score]:
    out: list[Score] = []
    for concern in exp.concerns:
        if concern.role == "gate":
            out.append(
                Score(
                    concern_id=concern.id,
                    unknown=True,
                    pass_=False,
                    value=None,
                    evidence={"killed_reason": reason},
                )
            )
    for gid in SYSTEM_GATES:
        out.append(Score(concern_id=gid, unknown=True, pass_=False, value=None, evidence={"killed_reason": reason}))
    return out


def score_concerns(
    trial: Trial,
    exp: Experiment,
    ctx: dict[str, str],
    env: dict[str, str],
) -> list[Score]:
    if trial.result and trial.case.require_exit_0 and trial.result.exit_code != 0:
        return fail_closed_for_gates(trial, exp, reason="require_exit_0")
    out: list[Score] = []
    for concern in exp.concerns:
        t = concern.measure.type
        if t == "llm_rubric":
            timeout = (concern.measure.timeout_s or (concern.judge.timeout_s if concern.judge else None) or (exp.judge.timeout_s if exp.judge else 180))
            out.append(spawn_judge(trial, concern, exp, int(timeout)))
        elif t == "script":
            timeout = concern.measure.timeout_s or 120
            out.append(run_script_measure(trial, concern, exp, ctx, env, int(timeout)))
        else:
            out.append(builtin_evaluate(trial, concern, exp, ctx))
    return out
