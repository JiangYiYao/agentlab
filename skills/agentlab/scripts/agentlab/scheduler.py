from __future__ import annotations

import json
import shutil
import threading
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from agentlab.adapters.artifact.dir import DirArtifact
from agentlab.adapters.evaluator.score import SYSTEM_GATES, fail_closed_for_gates, score_concerns
from agentlab.adapters.isolation.homedir import HomedirIsolation
from agentlab.adapters.isolation.tempdir import TempdirIsolation
from agentlab.adapters.isolation.worktree import (
    WorktreeIsolation,
    resolve_repo,
)
from agentlab.budget import BudgetTracker
from agentlab.envmerge import inherit_flag, isolation_overlays, merge_env
from agentlab.errors import AdapterError, BudgetExceeded, ContractError
from agentlab.expand import expand, filter_trials
from agentlab.flock import FileLock
from agentlab.gate import Promotion, TrialRecord, evaluate_promotion, gate_exit_code
from agentlab.leaks import (
    forbidden_executed_trees,
    leak_scores,
    path_in_trees,
    snapshot_forbidden_paths,
)
from agentlab.compare_judge import run_compare_judges
from agentlab.diffreport import write_run_diff, write_trial_diff
from agentlab.models import Score, Trial, Sandbox, RunnerResult, Usage
from agentlab.recipes import bound_command
from agentlab.execution_audit import capture_inputs, capture_trace
from agentlab.runner.shell import ShellRunner, athlete_argv
from agentlab.runs import archive_trial, latest_run_id, load_manifest, new_run_id, update_manifest, write_manifest, with_run_repetitions
from agentlab.schema import Experiment, fingerprint_contract, fingerprint_score_basis
from agentlab.templates import build_context
from agentlab.provenance import execution_basis, measurement_basis, atomic_json, tree_digest
from agentlab.evidence import capture_evidence
from agentlab.storage import init_storage, save_execution, latest_trial, materialize, finish_cache, execution_path


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
    from agentlab.workspace import hash_snapshot

    return hash_snapshot(trial.sandbox.project_root)


def _write_meta(trial: Trial, extra: dict[str, Any], *, score_basis: str | None = None) -> None:
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


def _write_scores(trial: Trial) -> None:
    payload = [s.to_json() for s in trial.scores]
    path = trial.trial_dir() / "scores.json"
    if path.is_file():
        try:
            if json.loads(path.read_text()) == payload:
                return
        except (OSError, ValueError):
            pass
    atomic_json(path, payload)


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
                candidate = next((t for t in expand(exp, root) if t.id == tid), None)
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


def run_experiment(
    exp: Experiment,
    root: Path,
    *,
    only_variant: str | None = None,
    only_cell: str | None = None,
    only_case: str | None = None,
    keep_sandbox: bool = False,
    dry_expand: bool = False,
    gate: bool = False,
    max_parallel: int | None = None,
    force: bool = False,
    repetitions: int | None = None,
    retry_failed: bool = False,
    no_reuse: bool = False,
    rescore: bool = False,
    source_run: str | None = None,
) -> tuple[int, Promotion | None, list[Trial]]:
    exp = freeze_experiment(exp, root)
    if repetitions is not None and repetitions < 1 or max_parallel is not None and max_parallel < 1:
        raise ContractError("unknown_field", "repetitions and parallelism must be positive")
    for chosen, items, label in ((only_variant, exp.variants, "variant"), (only_cell, exp.matrix.cells, "cell"), (only_case, exp.cases, "case")):
        if chosen and chosen not in {item.id for item in items}:
            raise ContractError("unknown_field", f"unknown {label}: {chosen}")
    overrides: dict[str, Any] = {}
    if repetitions is not None:
        exp = exp.model_copy(update={"repetitions": repetitions})
        overrides["repetitions"] = repetitions
    if max_parallel is not None:
        overrides["max_parallel"] = max_parallel
    if rescore:
        source_run = source_run or latest_run_id(root)
        source_manifest = load_manifest(root, source_run) if source_run else None
        if not source_manifest:
            raise ContractError("execution_unavailable", "rescore requires an existing run")
        exp = with_run_repetitions(exp, source_manifest)
        only_variant = only_variant or source_manifest.get("only_variant")
        only_cell = only_cell or source_manifest.get("only_cell")
        only_case = only_case or source_manifest.get("only_case")
    trials = filter_trials(
        expand(exp, root),
        only_variant=only_variant,
        only_cell=only_cell,
        only_case=only_case,
    )
    if rescore:
        wanted = set(source_manifest.get("planned") or [])
        trials = [t for t in trials if t.id in wanted]
    if dry_expand:
        for t in trials:
            print(f"{t.id}\t{t.variant.id}\t{t.cell.id}\t{t.case.id}\tr{t.repeat}")
        return 0, None, trials

    lock = FileLock(root / "run.lock")
    if not lock.acquire(blocking=False):
        raise ContractError("run_in_progress", "another agentlab run holds run.lock")
    budget_incomplete = False
    tracker = BudgetTracker(exp.budget)
    abort_env = threading.Event()
    tracker.cancel_event = abort_env
    planned_ids = [t.id for t in trials]
    run_id = new_run_id(root)
    manifest = {
        "run_id": run_id,
        "status": "running",
        "planned": planned_ids,
        "ran": [],
        "reused": [],
        "skipped": [],
        "env_unusable": [],
        "retried": [],
        "only_variant": only_variant,
        "only_cell": only_cell,
        "only_case": only_case,
        "overrides": overrides,
        "score_basis": fingerprint_score_basis(exp),
        "contract_hash": fingerprint_contract(exp),
        "experiment": exp.model_dump(mode="json", by_alias=True),
        "operation": "rescore" if rescore else "run",
        "source_run": source_run,
    }
    try:
        init_storage(root, run_id)
        write_manifest(root, manifest)
        atomic_json(root / "runs" / run_id / "experiment.json", exp.model_dump(mode="json", by_alias=True))
        shutil.copy2(root / exp.criteria.path, root / "runs" / run_id / "criteria.md")
        parallel = max_parallel or exp.budget.max_parallel
        leaks_before = snapshot_forbidden_paths()
        remaining = list(trials)

        def _refresh_manifest() -> None:
            update_manifest(
                root,
                run_id,
                ran=[t.id for t in trials if t.result is not None and not t.reused and not t.skipped],
                reused=[t.id for t in trials if t.reused],
                skipped=[t.id for t in trials if t.skipped],
                env_unusable=[t.id for t in trials if t.error_code == "env_unusable"],
                retried=[t.id for t in trials if getattr(t, "retried", False)],
            )

        def execute(trial):
            trial.budget_tracker = tracker
            return _run_one(exp, root, trial, tracker, leaks_before, keep_sandbox,
                            force=force, abort_env=abort_env, retry_failed=retry_failed,
                            no_reuse=no_reuse, run_id=run_id, rescore=rescore, source_run=source_run)

        if parallel <= 1:
            for trial in remaining:
                execute(trial)
                _refresh_manifest()
        else:
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futures = [pool.submit(execute, trial) for trial in remaining]
                try:
                    for future in as_completed(futures):
                        future.result()
                        _refresh_manifest()
                except BaseException:
                    abort_env.set()
                    for future in futures:
                        future.cancel()
                    raise

        compare_started = time.time()
        run_compare_judges(exp, root, trials, run_id)
        compare_s = time.time() - compare_started
        budget_incomplete = tracker.exceeded() or any(t.error_code == "budget_exceeded" for t in trials)
        for trial in trials:
            _write_meta(trial, {"run_id": run_id}, score_basis=fingerprint_score_basis(exp))
            archive_trial(root, run_id, trial.id, reused_from=trial.reused_from)
        records, stale = load_current_records(exp, root, trial_ids=planned_ids, run_id=run_id)
        only_v = {only_variant} if only_variant else None
        only_c = {only_cell} if only_cell else None
        only_k = {only_case} if only_case else None
        promo = evaluate_promotion(exp, records, only_variants=only_v, only_cells=only_c, only_cases=only_k)
        promo.ignored_stale = stale
        update_manifest(
            root,
            run_id,
            status="incomplete" if budget_incomplete or abort_env.is_set() else "done",
            rescored=[t.id for t in trials if t.rescored],
            elapsed_s=time.time() - tracker.started,
            compare_s=compare_s,
            execution_s=sum(t.stage_times.get("execution_s", 0) for t in trials if not t.reused),
            evaluation_s=sum(t.stage_times.get("evaluation_s", 0) for t in trials),
            usage={"usd": tracker.used_usd, "tokens": tracker.used_tokens, "unknown_calls": tracker.unknown_usage},
            ran=[t.id for t in trials if t.result is not None and not t.reused and not t.skipped],
            reused=[t.id for t in trials if t.reused],
            skipped=[t.id for t in trials if t.skipped],
            env_unusable=[t.id for t in trials if t.error_code == "env_unusable"],
            retried=[t.id for t in trials if t.retried],
        )
        promo_text = json.dumps(promo.to_json(), indent=2, ensure_ascii=False) + "\n"
        (root / "promotion.json").write_text(promo_text, encoding="utf-8")
        (root / "runs" / run_id / "promotion.json").write_text(promo_text, encoding="utf-8")
        all_skipped = bool(trials) and all(t.skipped or (t.result is None and not t.scores and not t.reused) for t in trials)
        if records and all(r.skipped or all(s.unknown for s in r.scores.values()) for r in records):
            all_skipped = True
        if not records:
            all_skipped = True
        env_incomplete = abort_env.is_set() or any(t.error_code == "env_unusable" for t in trials)
        code = gate_exit_code(
            promo,
            gate=gate,
            budget_incomplete=budget_incomplete,
            zero_trials=len(trials) == 0,
            all_skipped=all_skipped and gate,
            env_incomplete=env_incomplete,
        )
        write_run_diff(root, run_id, planned_ids)
        _print_summary(exp, records, promo, gate, trials=trials, run_id=run_id, root=root)
        for trial in trials:
            finish_cache(root, trial.id, run_id)
        return code, promo, trials
    except BaseException as exc:
        update_manifest(root, run_id, status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
        raise
    finally:
        lock.release()


def freeze_experiment(exp: Experiment, root: Path) -> Experiment:
    exp = exp.model_copy(deep=True)
    if exp.isolation.type == "git-worktree" or any(c.isolation and c.isolation.type == "git-worktree" for c in exp.cases):
        repo = resolve_repo(exp.isolation.repo or "", root)
        exp.isolation.repo = str(repo)
        exp.isolation.freeze = subprocess.check_output(["git", "-C", str(repo), "rev-parse", exp.isolation.freeze or "HEAD"], text=True).strip()
        for nested in exp.isolation.nested_repos or []:
            source = resolve_repo(nested.source, root)
            nested.source = str(source)
            nested.freeze = subprocess.check_output(["git", "-C", str(source), "rev-parse", nested.freeze or "HEAD"], text=True).strip()
    return exp


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


def _run_one(exp, root, trial, tracker, leaks_before, keep_sandbox, *, force=False,
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
        _write_meta(trial, {"workspace_snap": _workspace_snap(trial), "phase": "preparing"}, score_basis=fingerprint_score_basis(exp))
        runner = ShellRunner(exp)
        prompt_path = runner.prepare(trial, ctx)
        argv, mode, flag = athlete_argv(exp, trial, ctx)
        capture_inputs(trial, exp, program, prompt_path)
        def on_start(pid):
            _write_meta(trial, {"pid": pid, "pgid": pid, "phase": "running", "command": argv, "requested_model": trial.cell.model}, score_basis=fingerprint_score_basis(exp))
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
            _write_meta(trial, {"phase": "evaluating"}, score_basis=fingerprint_score_basis(exp))
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
        _write_meta(trial, {"phase": "skipped" if trial.skipped else "failed" if trial.error_code else "completed", "run_id": run_id}, score_basis=fingerprint_score_basis(exp))
        _write_scores(trial)
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


def _print_summary(
    exp: Experiment,
    records: list[TrialRecord],
    promo: Promotion,
    gate: bool,
    *,
    trials: list[Trial] | None = None,
    run_id: str | None = None,
    root: Path | None = None,
) -> None:
    if run_id:
        print(f"run_id: {run_id}")
    if run_id and root and (root / "runs" / run_id / "diff.html").is_file():
        print(f"diff: runs/{run_id}/diff.html")
    planned = [t.id for t in (trials or [])]
    if planned:
        reused = sum(1 for t in trials or [] if t.reused)
        ran = sum(1 for t in trials or [] if t.result is not None and not t.reused and not t.skipped)
        skipped = sum(1 for t in trials or [] if t.skipped)
        print(f"planned: {len(planned)}")
        print(f"ran: {ran}")
        print(f"reused: {reused}")
        print(f"skipped: {skipped}")
        retried = sum(1 for t in trials or [] if t.retried)
        if retried:
            print(f"retried: {retried}")
    env_hits = [t for t in (trials or []) if t.error_code == "env_unusable"]
    if env_hits:
        reason = env_hits[0].killed_reason or "env_unusable"
        print(f"env_unusable: {reason}")
    print(f"scored: {len(records)}")
    print(f"system_ok: {promo.system_ok}")
    if not promo.variants:
        print("promotable: n/a (no treatments in universe)")
        return
    for vid, vp in promo.variants.items():
        print(f"variant {vid}: promotable={vp.promotable} recommend_ship={vp.recommend_ship}")
        for cell, ok in vp.cell_pass.items():
            print(f"  cell {cell}: {'pass' if ok else 'fail'}")
    if gate:
        print(f"promotion.json written; ship={any(v.recommend_ship for v in promo.variants.values())}")
