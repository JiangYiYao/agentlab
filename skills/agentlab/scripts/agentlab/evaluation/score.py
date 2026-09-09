from __future__ import annotations

import time
from agentlab.errors import BudgetExceeded

from agentlab.evaluation.builtin import builtin_evaluate
from agentlab.evaluation.script import run_script_measure
from agentlab.evaluation.judge import spawn_judge
from agentlab.models import Score, Trial, SYSTEM_GATES
from agentlab.schema import Experiment, judge_mode
from agentlab.records.provenance import measurement_basis


def fail_closed_for_gates(trial: Trial, exp: Experiment, *, reason: str) -> list[Score]:
    out: list[Score] = []
    for concern in exp.concerns:
        if concern.role == "gate":
            trial.record_evaluation(concern.id, concern.measure.type, "not_run", reason=reason)
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
        trial.record_evaluation(gid, "system", "not_run", reason=reason)
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
        if trial.budget_tracker and trial.budget_tracker.exceeded():
            raise BudgetExceeded(trial.budget_tracker.exceeded_reason)
        basis = measurement_basis(exp, trial, concern)
        cached = trial.cached_scores.get(concern.id)
        if not (trial.force_score and (concern.measure.type == "llm_rubric" or (cached is not None and cached.unknown))) and cached is not None and trial.measurement_basis.get(concern.id) == basis:
            trial.record_evaluation(concern.id, concern.measure.type, "reused")
            out.append(cached)
            continue
        trial.measurement_basis[concern.id] = basis
        t = concern.measure.type
        needs_workspace = t in {"gold_tree", "must_list", "workspace_diff"} or (t == "script" and concern.measure.cwd == "sandbox")
        if trial.reused and needs_workspace and trial.sandbox is None:
            trial.record_evaluation(concern.id, t, "not_run", reason="complete workspace evidence unavailable")
            out.append(Score(concern_id=concern.id, unknown=True, pass_=False,
                             evidence={"error": "complete workspace evidence unavailable; run with evidence.workspace: true to support this evaluator"}))
            continue
        if t == "llm_rubric":
            if judge_mode(exp) == "compare_case":
                continue
            trial.record_evaluation(concern.id, t, "evaluated")
            timeout = (concern.measure.timeout_s or (concern.judge.timeout_s if concern.judge else None) or (exp.judge.timeout_s if exp.judge else 180))
            out.append(spawn_judge(trial, concern, exp, int(timeout)))
        elif t == "script":
            trial.record_evaluation(concern.id, t, "evaluated")
            timeout = concern.measure.timeout_s or 120
            if trial.budget_tracker and exp.budget.wall_clock_s is not None:
                timeout = min(timeout, max(0.01, trial.budget_tracker.started + exp.budget.wall_clock_s - time.time()))
            out.append(run_script_measure(trial, concern, exp, ctx, env, timeout))
        else:
            trial.record_evaluation(concern.id, t, "evaluated")
            out.append(builtin_evaluate(trial, concern, exp, ctx))
    return out
