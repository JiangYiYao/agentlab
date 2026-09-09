"""Execute or restore one trial, capture its evidence, and score it."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from agentlab.adapters.artifact.dir import DirArtifact
from agentlab.evaluation.score import fail_closed_for_gates, score_concerns
from agentlab.adapters.isolation.homedir import HomedirIsolation
from agentlab.adapters.isolation.tempdir import TempdirIsolation
from agentlab.adapters.isolation.worktree import (
    WorktreeIsolation,
    resolve_repo,
)
from agentlab.execution.envmerge import inherit_flag, isolation_overlays, merge_env
from agentlab.errors import AdapterError, BudgetExceeded, ContractError
from agentlab.execution.leaks import (
    forbidden_executed_trees,
    leak_scores,
    path_in_trees,
    snapshot_forbidden_paths,
)
from agentlab.reporting.changes import write_trial_diff
from agentlab.models import Score, Trial, Sandbox, RunnerResult, Usage, SYSTEM_GATES
from agentlab.recipes import bound_command
from agentlab.execution.capture import capture_inputs, capture_trace
from agentlab.execution.shell import ShellRunner, athlete_argv
from agentlab.records.runs import archive_trial
from agentlab.schema import Experiment, fingerprint_score_basis
from agentlab.templates import build_context
from agentlab.records.provenance import execution_basis, measurement_basis, tree_digest
from agentlab.evaluation.evidence import capture_evidence
from agentlab.records.storage import save_execution, latest_trial, materialize, execution_path
from agentlab.records.runs import write_trial_meta, write_trial_scores


def _iso_kind(exp: Experiment, trial: Trial) -> str:
    if trial.case.isolation and trial.case.isolation.type:
        return trial.case.isolation.type
    return exp.isolation.type


def _inherit(exp: Experiment, trial: Trial, recipe_flag: bool | None) -> bool:
    case_flag = trial.case.isolation.inherit_host_identity if trial.case.isolation else None
    cell_flag = trial.cell.inherit_host_identity
    if cell_flag is not None:
        return inherit_flag(cell_flag, recipe_flag, exp.isolation.inherit_host_identity)
    if case_flag is not None:
        return inherit_flag(None, recipe_flag, case_flag)
    return inherit_flag(None, recipe_flag, exp.isolation.inherit_host_identity)


def _make_isolation(exp: Experiment, trial: Trial, root: Path):
    kind = _iso_kind(exp, trial)
    if kind == "git-worktree":
        repo = exp.isolation.repo or ""
        repo_path = Path(repo) if Path(repo).is_absolute() else (root / repo).resolve()
        return WorktreeIsolation(
            repo=repo_path,
            freeze=exp.isolation.freeze,
            subdir=exp.isolation.subdir,
            nested_repos=list(exp.isolation.nested_repos or []),
            experiment_root=root,
        )
    if kind == "homedir":
        return HomedirIsolation()
    return TempdirIsolation()


def _workspace_snap(trial: Trial) -> dict[str, str]:
    if trial.sandbox is None:
        return {}
    from agentlab.execution.workspace import hash_snapshot

    return hash_snapshot(trial.sandbox.project_root)


def _restore_completed(trial: Trial, exp: Experiment, *, retry_failed=False, source_run=None) -> bool:
    root = trial.experiment_root
    src = root / "runs" / source_run / "trials" / trial.id if source_run else trial.trial_dir()
    if not (src / "meta.json").is_file() and not source_run:
        saved = latest_trial(root, trial.id)
        if saved:
            src = saved
            source_run = saved.parent.parent.name
    try:
        meta = json.loads((src / "meta.json").read_text())
        scores = [Score.from_json(x) for x in json.loads((src / "scores.json").read_text())]
    except (OSError, ValueError, TypeError):
        return False
    if meta.get("skipped") or "exit_code" not in meta or not meta.get("execution_id") or meta.get("execution_basis") != trial.execution_basis:
        return False
    if meta.get("evidence_digest") and tree_digest(src / "outputs" / "evidence") != meta["evidence_digest"]:
        return False
    if retry_failed and (meta.get("execution_error") or meta.get("error_code") in {"isolation_leak", "wrong_skill_tree", "sandbox_create_failed", "command_nonzero", "command_timeout", "bin_not_found", "env_unusable"}):
        trial.retried = True
        return False
    if source_run and src != trial.trial_dir():
        materialize(src, trial.trial_dir())
    if source_run:
        # Paths in an archive point at that archive; new scoring uses the restored working copy.
        for score in scores:
            score.evidence = json.loads(json.dumps(score.evidence).replace(str(src) + "/outputs/", str(trial.trial_dir()) + "/outputs/"))
    trial.cached_scores = {x.concern_id: x for x in scores}
    trial.scores = scores
    trial.measurement_basis = meta.get("measurement_basis") or {}
    trial.compare_basis = meta.get("compare_basis")
    trial.previous_evaluations = meta.get("evaluation_events") or {}
    trial.execution_id = meta["execution_id"]
    trial.reused_from = source_run or meta.get("run_id")
    trial.error_code = meta.get("execution_error") or (meta.get("error_code") if meta.get("error_code") in {"isolation_leak", "wrong_skill_tree"} else None)
    trial.killed_reason = meta.get("killed_reason")
    trial.freeze_sha = meta.get("freeze_sha")
    trial.result = RunnerResult(exit_code=meta.get("exit_code", 127), stdout_path=trial.outputs_dir()/"stdout.log",
                               stderr_path=trial.outputs_dir()/"stderr.log", usage=Usage(**(meta.get("usage") or {})),
                               wall_clock_s=meta.get("wall_clock_s", 0), error_code=trial.error_code,
                               killed_reason=trial.killed_reason)
    # Re-evaluation consumes frozen evidence, never a retained, editable worktree.
    workspace = trial.outputs_dir() / "evidence" / "workspace"
    evidence_manifest = trial.outputs_dir() / "evidence" / "manifest.json"
    entries = json.loads(evidence_manifest.read_text()).get("entries", []) if evidence_manifest.is_file() else []
    workspace_complete = not any(e.get("path", "").startswith("workspace/") and (e.get("truncated") or e.get("missing")) for e in entries)
    if workspace.is_dir() and workspace_complete:
        trial.sandbox = Sandbox(root=workspace, project_root=workspace)
    return True


def _trial_context(exp, root, trial):
    project = trial.sandbox.project_root if trial.sandbox else trial.outputs_dir() / "unavailable-workspace"
    sandbox = trial.sandbox.root if trial.sandbox else project
    program = project if exp.artifact.layout == "inplace" else trial.outputs_dir() / "program"
    case_path = (root / (trial.case.path or f"cases/{trial.case.id}")).resolve()
    ctx = build_context(exp=exp, experiment_root=root, variant_id=trial.variant.id, cell_id=trial.cell.id,
                        case_id=trial.case.id, trial_id=trial.id, cell_model=trial.cell.model,
                        case_path=str(case_path), sandbox=sandbox, project_root=project,
                        trial_out=trial.outputs_dir(), program_root=program)
    _, recipe = bound_command(exp, trial.cell, trial.case, root)
    inherit = _inherit(exp, trial, recipe.inherit_host_identity if recipe else None)
    overlays = isolation_overlays(experiment_root=root, project_root=project, trial_out=trial.outputs_dir(),
                                  program_root=program, case_path=case_path, extra=exp.isolation.env_inject)
    home = trial.sandbox.home if trial.sandbox else None
    if not inherit and home is None:
        home = trial.outputs_dir()/"evaluation-home"
        home.mkdir(exist_ok=True)
    env = merge_env(overlays=overlays, recipe_env=recipe.env if recipe else None,
                    cell_env=trial.cell.env, case_env=trial.case.env, ctx=ctx,
                    inherit_home=inherit, sandbox_home=home)
    ctx.update({k: v for k, v in env.items() if k.startswith("AGENTLAB_")})
    return ctx, env


def _protected_snapshot(exp, root):
    paths = list(exp.isolation.protected_paths)
    if exp.artifact.source_path:
        paths.append(exp.artifact.source_path)
    if exp.isolation.repo:
        paths.append(exp.isolation.repo)
    paths.extend(n.source for n in exp.isolation.nested_repos or [])
    return {str(resolve_repo(p, root)): tree_digest(resolve_repo(p, root)) for p in paths}


def run_trial(exp, root, trial, tracker, leaks_before, keep_sandbox, *, force=False,
             abort_env=None, retry_failed=False, no_reuse=False, run_id=None, rescore=False, source_run=None):
    iso = None
    protected = {}
    trial.budget_tracker = tracker
    trial.run_id = run_id
    try:
        trial.execution_basis = execution_basis(exp, trial)
        protected = _protected_snapshot(exp, root)
        if abort_env is not None and abort_env.is_set():
            trial.skipped = True
            trial.error_code = "env_unusable"
            raise AdapterError("env_unusable", "batch stopped after environment failure")
        reused = not force and not no_reuse and _restore_completed(trial, exp, retry_failed=retry_failed, source_run=source_run)
        if rescore and not reused:
            raise ContractError("execution_unavailable", "rescore requires a matching archived execution; it never starts the tested command")
        trial.reused = reused
        if reused:
            trial.rescored = rescore or any(trial.measurement_basis.get(c.id) != measurement_basis(exp, trial, c) for c in exp.concerns)
            trial.force_score = rescore or retry_failed
            # Runtime failure is evidence; changing a rubric must not erase it.
            if trial.error_code:
                trial.scores = fail_closed_for_gates(trial, exp, reason=trial.error_code)
            else:
                ctx, env = _trial_context(exp, root, trial)
                started = time.time()
                trial.scores = score_concerns(trial, exp, ctx, env)
                trial.stage_times["evaluation_s"] = time.time() - started
                for gid in SYSTEM_GATES:
                    if gid not in trial.evaluation_events:
                        trial.record_evaluation(gid, "system", "reused" if gid in trial.cached_scores else "not_run")
                    trial.scores.append(trial.cached_scores.get(gid, Score(concern_id=gid, unknown=True, pass_=False)))
            return
        # Every physical execution owns a fresh workspace. Kept worktrees remain in their original run.
        if trial.trial_dir().exists():
            shutil.rmtree(trial.trial_dir())
        trial.outputs_dir().mkdir(parents=True)
        trial.execution_id = f"{run_id}:{trial.id}"
        trial.sandbox_path = root / "workspaces" / run_id / trial.id
        iso = _make_isolation(exp, trial, root)
        _, recipe = bound_command(exp, trial.cell, trial.case, root)
        inherit = _inherit(exp, trial, recipe.inherit_host_identity if recipe else None)
        with iso.worktree_lock():
            if getattr(iso, "type", "") == "homedir":
                trial.sandbox = iso.create(trial, inherit_host_identity=inherit)
            else:
                trial.sandbox = iso.create(trial)
        if not inherit and trial.sandbox.home is None:
            trial.sandbox.home = trial.outputs_dir() / "home"
            trial.sandbox.home.mkdir(exist_ok=True)
        program = trial.program_root(exp, trial.sandbox)
        DirArtifact().materialize(trial.variant, program, root)
        ctx, env = _trial_context(exp, root, trial)
        write_trial_meta(trial, {"workspace_snap": _workspace_snap(trial), "phase": "preparing"}, score_basis=fingerprint_score_basis(exp))
        runner = ShellRunner(exp)
        prompt_path = runner.prepare(trial, ctx)
        argv, mode, flag = athlete_argv(exp, trial, ctx)
        capture_inputs(trial, exp, program, prompt_path)
        def on_start(pid):
            write_trial_meta(trial, {"pid": pid, "pgid": pid, "phase": "running", "command": argv, "requested_model": trial.cell.model}, score_basis=fingerprint_score_basis(exp))
        with tracker.trial_watch(trial):
            deadline = tracker.trial_deadline()
            budget_deadline = deadline
            if trial.case.timeout_s:
                deadline = min(deadline or float("inf"), time.time() + trial.case.timeout_s)
            trial.result = runner.run(trial, argv=argv, cwd=trial.sandbox.project_root, env=env,
                                      prompt_path=prompt_path, deadline=deadline, prompt_mode=mode,
                                      prompt_flag=flag, on_start=on_start, cancel_event=abort_env)
        trial.error_code = trial.result.error_code
        trial.killed_reason = trial.result.killed_reason
        if trial.error_code == "command_timeout" and budget_deadline is not None and deadline == budget_deadline:
            trial.error_code = "budget_exceeded"
            tracker.exceeded_reason = "timeout"
        trial.stage_times["execution_s"] = trial.result.wall_clock_s
        write_trial_diff(trial)
        capture_trace(trial, exp)
        capture_evidence(trial, exp)
        save_execution(trial)
        if trial.error_code:
            if trial.error_code == "env_unusable" and abort_env is not None:
                abort_env.set()
            trial.scores = fail_closed_for_gates(trial, exp, reason=trial.error_code)
        else:
            write_trial_meta(trial, {"phase": "evaluating"}, score_basis=fingerprint_score_basis(exp))
            started = time.time()
            trial.scores = score_concerns(trial, exp, ctx, env)
            trial.stage_times["evaluation_s"] = time.time() - started
            leaked = leak_scores(leaks_before, snapshot_forbidden_paths())
            changed = [path for path, old in protected.items() if tree_digest(Path(path)) != old]
            if changed:
                leaked = True
            replay = trial.outputs_dir() / "replay.json"
            script = json.loads(replay.read_text()).get("prepare_script") if replay.is_file() else None
            wrong_tree = bool(script and path_in_trees(Path(script), forbidden_executed_trees(exp.artifact.source_path, root)))
            # Never overwrite a failed system check with a second check of the same name.
            known = {score.concern_id for score in trial.scores}
            for gid, ok in (("__isolation_leak__", not leaked), ("__wrong_skill_tree__", not wrong_tree)):
                if gid not in known:
                    trial.record_evaluation(gid, "system", "evaluated")
                    trial.scores.append(Score(concern_id=gid, value=ok, pass_=ok, evidence={"changed_protected_paths": changed} if leaked else {}))
            if leaked or wrong_tree:
                trial.error_code = "isolation_leak" if leaked else "wrong_skill_tree"
    except BudgetExceeded as exc:
        trial.error_code = "budget_exceeded"
        trial.killed_reason = exc.reason
        trial.skipped = trial.result is None
        trial.scores = fail_closed_for_gates(trial, exp, reason=exc.reason)
    except Exception as exc:
        trial.error_code = getattr(exc, "code", "eval_failed")
        trial.scores = fail_closed_for_gates(trial, exp, reason=str(exc))
    finally:
        changed = [path for path, before in protected.items() if tree_digest(Path(path)) != before]
        if changed:
            trial.error_code = "isolation_leak"
            trial.record_evaluation("__isolation_leak__", "system", "evaluated")
            trial.scores = [s for s in trial.scores if s.concern_id != "__isolation_leak__"]
            trial.scores.append(Score(concern_id="__isolation_leak__", value=False, pass_=False,
                                      evidence={"changed_protected_paths": changed}))
        trial.trial_dir().mkdir(parents=True, exist_ok=True)
        write_trial_meta(trial, {"phase": "skipped" if trial.skipped else "failed" if trial.error_code else "completed", "run_id": run_id}, score_basis=fingerprint_score_basis(exp))
        write_trial_scores(trial)
        if trial.execution_id and not trial.reused and not (execution_path(root, trial.execution_id) / "manifest.json").is_file():
            capture_trace(trial, exp)
            save_execution(trial)
        if run_id:
            archive_trial(root, run_id, trial.id, reused_from=trial.reused_from)
        if iso and trial.sandbox and not trial.reused:
            keep = keep_sandbox or exp.isolation.keep_sandbox or bool(trial.error_code and exp.isolation.keep_on_fail)
            if not keep:
                with iso.worktree_lock():
                    iso.destroy(trial.sandbox)
