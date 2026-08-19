from __future__ import annotations

import hashlib
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agentlab.adapters.artifact.dir import DirArtifact
from agentlab.adapters.evaluator.score import SYSTEM_GATES, fail_closed_for_gates, score_concerns
from agentlab.adapters.isolation.homedir import HomedirIsolation
from agentlab.adapters.isolation.tempdir import TempdirIsolation
from agentlab.adapters.isolation.worktree import (
    WorktreeIsolation,
    prune_worktrees,
    remove_worktree,
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
from agentlab.models import Score, Trial
from agentlab.recipes import bound_command
from agentlab.runner.shell import ShellRunner, athlete_argv
from agentlab.schema import Experiment, fingerprint_contract, fingerprint_score_basis
from agentlab.templates import build_context


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
        return WorktreeIsolation(repo=repo_path, freeze=exp.isolation.freeze, subdir=exp.isolation.subdir)
    if kind == "homedir":
        return HomedirIsolation()
    return TempdirIsolation()


def _workspace_snap(trial: Trial) -> list[str]:
    if trial.sandbox is None:
        return []
    root = trial.sandbox.project_root
    names = []
    for file in root.rglob("*"):
        if file.is_file() and ".git" not in file.parts:
            names.append(file.relative_to(root).as_posix())
    return sorted(names)


def _write_meta(trial: Trial, extra: dict[str, Any], *, score_basis: str | None = None) -> None:
    meta = {
        "trial_id": trial.id,
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
        **extra,
    }
    if trial.result:
        meta["exit_code"] = trial.result.exit_code
        meta["wall_clock_s"] = trial.result.wall_clock_s
        meta["error_code"] = trial.result.error_code or trial.error_code
    path = trial.trial_dir() / "meta.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_scores(trial: Trial) -> None:
    payload = [s.to_json() for s in trial.scores]
    (trial.trial_dir() / "scores.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _meta_current(meta: dict[str, Any], exp: Experiment) -> bool:
    basis = fingerprint_score_basis(exp)
    if meta.get("score_basis") == basis:
        return True
    if not meta.get("score_basis") and meta.get("contract_hash") == fingerprint_contract(exp):
        return True
    return False


def load_current_records(exp: Experiment, root: Path) -> tuple[list[TrialRecord], list[str]]:
    records: list[TrialRecord] = []
    stale: list[str] = []
    trials_dir = root / "trials"
    if not trials_dir.is_dir():
        return records, stale
    for meta_path in trials_dir.glob("*/meta.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        scores_path = meta_path.parent / "scores.json"
        if not _meta_current(meta, exp):
            stale.append(meta.get("trial_id", meta_path.parent.name))
            continue
        if not scores_path.is_file():
            continue
        raw = json.loads(scores_path.read_text(encoding="utf-8"))
        scores = {item["concern_id"]: Score.from_json(item) for item in raw}
        records.append(
            TrialRecord(
                trial_id=meta["trial_id"],
                variant_id=meta["variant_id"],
                cell_id=meta["cell_id"],
                case_id=meta["case_id"],
                repeat=int(meta.get("repeat", 1)),
                role=meta.get("role", "treatment"),
                scores=scores,
                skipped=bool(meta.get("skipped")),
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
) -> tuple[int, Promotion | None, list[Trial]]:
    trials = filter_trials(
        expand(exp, root),
        only_variant=only_variant,
        only_cell=only_cell,
        only_case=only_case,
    )
    if dry_expand:
        for t in trials:
            print(f"{t.id}\t{t.variant.id}\t{t.cell.id}\t{t.case.id}\tr{t.repeat}")
        return 0, None, trials

    lock = FileLock(root / "run.lock")
    if not lock.acquire(blocking=False):
        raise ContractError("run_in_progress", "another agentlab run holds run.lock")
    budget_incomplete = False
    abort_env = threading.Event()
    try:
        _prune_orphans(exp, root)
        tracker = BudgetTracker(exp.budget)
        parallel = max_parallel or exp.budget.max_parallel
        leaks_before = snapshot_forbidden_paths()
        remaining = list(trials)
        if parallel <= 1:
            for trial in remaining:
                if abort_env.is_set():
                    _skip_rest(remaining[remaining.index(trial) :], tracker, exp, reason="env_unusable")
                    break
                if tracker.exceeded():
                    budget_incomplete = _skip_rest(remaining[remaining.index(trial) :], tracker, exp)
                    break
                _run_one(
                    exp, root, trial, tracker, leaks_before, keep_sandbox, force=force, abort_env=abort_env
                )
        else:
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futs = []
                for trial in remaining:
                    if tracker.exceeded():
                        budget_incomplete = _skip_rest(
                            [t for t in remaining if t.result is None and not t.scores], tracker, exp
                        )
                        break
                    futs.append(
                        pool.submit(
                            _run_one,
                            exp,
                            root,
                            trial,
                            tracker,
                            leaks_before,
                            keep_sandbox,
                            force=force,
                            abort_env=abort_env,
                        )
                    )
                for fut in as_completed(futs):
                    fut.result()

        records, stale = load_current_records(exp, root)
        only_v = {only_variant} if only_variant else None
        only_c = {only_cell} if only_cell else None
        only_k = {only_case} if only_case else None
        promo = evaluate_promotion(exp, records, only_variants=only_v, only_cells=only_c, only_cases=only_k)
        promo.ignored_stale = stale
        if gate:
            (root / "promotion.json").write_text(json.dumps(promo.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        finished = [t for t in trials if not t.skipped]
        all_skipped = bool(trials) and all(t.skipped or (t.result is None and not t.scores) for t in trials)
        # reload skipped flags from disk
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
        _print_summary(exp, records, promo, gate, trials=trials)
        return code, promo, trials
    finally:
        lock.release()


def _skip_rest(
    rest: list[Trial], tracker: BudgetTracker, exp: Experiment, *, reason: str | None = None
) -> bool:
    for trial in rest:
        if trial.scores or trial.result:
            continue
        trial.skipped = True
        trial.killed_reason = reason or tracker.exceeded_reason or "budget_experiment"
        if reason == "env_unusable":
            trial.error_code = "env_unusable"
        trial.scores = fail_closed_for_gates(trial, exp, reason=trial.killed_reason)
        trial.trial_dir().mkdir(parents=True, exist_ok=True)
        _write_meta(trial, {}, score_basis=fingerprint_score_basis(exp))
        _write_scores(trial)
    return True


def _prune_orphans(exp: Experiment, root: Path) -> None:
    trials_dir = root / "trials"
    if exp.isolation.type == "git-worktree" and exp.isolation.repo:
        repo = resolve_repo(exp.isolation.repo, root)
        if repo.exists():
            iso = WorktreeIsolation(repo=repo)
            with iso.worktree_lock():
                if trials_dir.is_dir():
                    for sandbox in trials_dir.glob("*/sandbox"):
                        if not (sandbox.parent / "scores.json").is_file():
                            remove_worktree(repo, sandbox)
                prune_worktrees(repo)
        return
    if not trials_dir.is_dir():
        return
    for sandbox in trials_dir.glob("*/sandbox"):
        if not (sandbox.parent / "scores.json").is_file():
            shutil.rmtree(sandbox, ignore_errors=True)


def _reuse_completed(trial: Trial, exp: Experiment) -> bool:
    meta_path = trial.trial_dir() / "meta.json"
    scores_path = trial.trial_dir() / "scores.json"
    if not meta_path.is_file() or not scores_path.is_file():
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("skipped"):
        return False
    if not _meta_current(meta, exp):
        return False
    raw = json.loads(scores_path.read_text(encoding="utf-8"))
    trial.scores = [Score.from_json(item) for item in raw]
    return True


def _run_one(
    exp: Experiment,
    root: Path,
    trial: Trial,
    tracker: BudgetTracker,
    leaks_before: dict[str, str],
    keep_sandbox: bool,
    *,
    force: bool = False,
    abort_env: threading.Event | None = None,
) -> None:
    if abort_env is not None and abort_env.is_set():
        trial.skipped = True
        trial.error_code = "env_unusable"
        trial.killed_reason = "env_unusable"
        trial.scores = fail_closed_for_gates(trial, exp, reason="env_unusable")
        trial.trial_dir().mkdir(parents=True, exist_ok=True)
        _write_meta(trial, {}, score_basis=fingerprint_score_basis(exp))
        _write_scores(trial)
        return
    if not force and _reuse_completed(trial, exp):
        return
    trial.trial_dir().mkdir(parents=True, exist_ok=True)
    trial.outputs_dir().mkdir(parents=True, exist_ok=True)
    iso = _make_isolation(exp, trial, root)
    _, recipe = bound_command(exp, trial.cell, trial.case, root)
    inherit = _inherit(exp, trial, recipe.inherit_host_identity if recipe else None)
    sandbox = None
    try:
        if getattr(iso, "type", "") == "git-worktree":
            with iso.worktree_lock():
                sandbox = iso.create(trial)
        elif getattr(iso, "type", "") == "homedir":
            sandbox = iso.create(trial, inherit_host_identity=inherit)
        else:
            sandbox = iso.create(trial)
        trial.sandbox = sandbox
        program = trial.program_root(exp, sandbox)
        DirArtifact().materialize(trial.variant, program, root)
        case_path = (root / (trial.case.path or f"cases/{trial.case.id}")).resolve()
        extra = dict(exp.isolation.env_inject or {})
        overlays = isolation_overlays(
            experiment_root=root,
            project_root=sandbox.project_root,
            trial_out=trial.outputs_dir(),
            program_root=program,
            case_path=case_path,
            extra=extra,
        )
        ctx = build_context(
            exp=exp,
            experiment_root=root,
            variant_id=trial.variant.id,
            cell_id=trial.cell.id,
            case_id=trial.case.id,
            trial_id=trial.id,
            cell_model=trial.cell.model,
            case_path=str(case_path),
            sandbox=sandbox.root,
            project_root=sandbox.project_root,
            trial_out=trial.outputs_dir(),
            program_root=program,
        )
        env = merge_env(
            overlays=overlays,
            recipe_env=recipe.env if recipe else None,
            cell_env=trial.cell.env,
            case_env=trial.case.env,
            ctx=ctx,
            inherit_home=inherit,
            sandbox_home=sandbox.home,
        )
        # persist workspace snap for workspace_diff
        snap = _workspace_snap(trial)
        meta_pre = {"workspace_snap": snap, "worktree": bool(sandbox.worktree)}
        _write_meta(trial, meta_pre, score_basis=fingerprint_score_basis(exp))
        runner = ShellRunner(exp)
        prompt_path = runner.prepare(trial, ctx)
        argv, mode, flag = athlete_argv(exp, trial, ctx)
        with tracker.trial_watch(trial):
            trial.result = runner.run(
                trial,
                argv=argv,
                cwd=sandbox.project_root,
                env=env,
                prompt_path=prompt_path,
                deadline=tracker.trial_deadline(),
                prompt_mode=mode,
                prompt_flag=flag,
            )
            if trial.result and trial.result.error_code:
                trial.error_code = trial.result.error_code
                trial.killed_reason = trial.result.killed_reason or trial.killed_reason
            if trial.error_code == "env_unusable":
                if abort_env is not None:
                    abort_env.set()
                trial.scores = fail_closed_for_gates(
                    trial, exp, reason=trial.killed_reason or "env_unusable"
                )
            else:
                trial.scores = score_concerns(trial, exp, ctx, env)
        leaks_after = snapshot_forbidden_paths()
        leaked = leak_scores(leaks_before, leaks_after)
        wrong_tree = False
        replay_path = trial.outputs_dir() / "replay.json"
        trees = forbidden_executed_trees(exp.artifact.source_path, root)
        if replay_path.is_file():
            data = json.loads(replay_path.read_text(encoding="utf-8"))
            script = data.get("prepare_script")
            if script and path_in_trees(Path(script), trees):
                wrong_tree = True
        leak_score = Score(concern_id="__isolation_leak__", value=not leaked, pass_=not leaked, unknown=False)
        tree_score = Score(concern_id="__wrong_skill_tree__", value=not wrong_tree, pass_=not wrong_tree, unknown=False)
        trial.scores.extend([leak_score, tree_score])
        if leaked:
            trial.error_code = "isolation_leak"
        if wrong_tree:
            trial.error_code = "wrong_skill_tree"
    except BudgetExceeded as exc:
        trial.killed_reason = exc.reason
        trial.error_code = "budget_exceeded"
        trial.scores = fail_closed_for_gates(trial, exp, reason=exc.reason)
    except (AdapterError, ContractError) as exc:
        trial.error_code = getattr(exc, "code", "sandbox_create_failed")
        trial.scores = fail_closed_for_gates(trial, exp, reason=str(exc))
    except Exception as exc:
        trial.error_code = "eval_failed"
        trial.scores = fail_closed_for_gates(trial, exp, reason=str(exc))
    finally:
        _write_meta(
            trial,
            {"workspace_snap": _workspace_snap(trial) if trial.sandbox else []},
            score_basis=fingerprint_score_basis(exp),
        )
        if trial.scores:
            _write_scores(trial)
        keep = keep_sandbox or exp.isolation.keep_sandbox
        failed = trial.error_code or (trial.result and trial.result.exit_code != 0)
        if not keep and not (failed and exp.isolation.keep_on_fail):
            if trial.sandbox is not None:
                if getattr(iso, "type", "") == "git-worktree":
                    with iso.worktree_lock():
                        iso.destroy(trial.sandbox)
                else:
                    iso.destroy(trial.sandbox)


def _print_summary(
    exp: Experiment,
    records: list[TrialRecord],
    promo: Promotion,
    gate: bool,
    *,
    trials: list[Trial] | None = None,
) -> None:
    env_hits = [t for t in (trials or []) if t.error_code == "env_unusable"]
    if env_hits:
        reason = env_hits[0].killed_reason or "env_unusable"
        print(f"env_unusable: {reason}")
        skipped = sum(1 for t in (trials or []) if t.skipped)
        if skipped:
            print(f"skipped_after_env_unusable: {skipped}")
    print(f"trials_scored: {len(records)}")
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
