"""Coordinate a run: select trials, schedule work, evaluate groups, and finalize."""
from __future__ import annotations

import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agentlab.execution.budget import BudgetTracker
from agentlab.errors import ContractError
from agentlab.execution.expand import expand, filter_trials
from agentlab.records.flock import FileLock
from agentlab.evaluation.gate import Promotion, TrialRecord, evaluate_promotion, gate_exit_code
from agentlab.execution.leaks import snapshot_forbidden_paths
from agentlab.evaluation.compare import run_compare_judges
from agentlab.reporting.changes import write_run_diff
from agentlab.models import Trial
from agentlab.records.runs import archive_trial, latest_run_id, load_manifest, new_run_id, update_manifest, write_manifest, with_run_repetitions
from agentlab.schema import Experiment, fingerprint_contract, fingerprint_score_basis
from agentlab.records.provenance import atomic_json
from agentlab.records.reader import load_current_records
from agentlab.records.provenance import freeze_experiment
from agentlab.records.storage import init_storage, finish_cache
from agentlab.execution.trial import run_trial
from agentlab.records.runs import write_trial_meta


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
            return run_trial(exp, root, trial, tracker, leaks_before, keep_sandbox,
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
            write_trial_meta(trial, {"run_id": run_id}, score_basis=fingerprint_score_basis(exp))
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
