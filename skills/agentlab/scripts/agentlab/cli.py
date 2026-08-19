"""agentlab CLI: brief, run, report, promote."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

from agentlab.errors import ContractError
from agentlab.paths import resolve_exp_dir
from agentlab.promote import promote
from agentlab.report import write_report
from agentlab.adapters.isolation.worktree import cleanup_experiment_worktrees, resolve_repo
from agentlab.scheduler import run_experiment
from agentlab.schema import SCHEMA_VERSION, SLUG, fingerprint_contract, trial_count
from agentlab.secrets_scan import scan_experiment_secrets
from agentlab.stats import preview_cost
from agentlab.validate import load_experiment, load_raw, validate_experiment, write_criteria_hash

HINTS = {
    "missing_criteria": "Add criteria.md and run `agentlab brief --confirm-criteria`.",
    "criteria_hash_mismatch": "criteria.md changed; re-run `agentlab brief --confirm-criteria`.",
    "need_exactly_one_baseline": "Keep exactly one variant with role: baseline.",
    "too_many_treatments": "At most 3 treatments; split into another experiment.",
    "too_many_concerns": "Keep 1–8 concerns.",
    "budget_reserve_required": "If you set budget.usd/tokens, also set per_trial.usd/tokens.",
    "reserved_env_key": "Do not set identity or isolation overlay keys on cell/case env.",
    "recipe_env_not_allowed": "recipe.env may only set CODEX_HOME or CLAUDE_CONFIG_DIR.",
    "unknown_cell_in_rule": "cell_rule.require must list existing cell ids.",
    "unknown_concern_in_accept": "accept_soft_gates must list existing concern ids.",
    "missing_judge_command": "llm_rubric needs judge.command at experiment or concern level.",
    "bin_not_on_path": "Install the CLI or point command[0] at an executable on PATH.",
    "unknown_field": "Remove forbidden keys (harness, skill_install, rubric, swap_order, slices).",
    "secrets_in_experiment": "Remove .env / secrets.env / key material from the experiment dir.",
}


def _print_contract_error(exc: ContractError) -> None:
    print(exc.format_line(), file=sys.stderr)
    hint = HINTS.get(exc.code)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)


def _slug_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not SLUG.match(slug):
        slug = ("exp-" + slug)[:64].strip("-")
    if not SLUG.match(slug):
        slug = "draft-experiment"
    return slug


def _dir_nonempty(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _copy_tree(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _init_from(exp_dir: Path, src: Path) -> None:
    if not src.is_dir():
        raise ContractError("artifact_missing_dir", f"--init-from is not a directory: {src}")
    exp_dir.mkdir(parents=True, exist_ok=True)
    baseline = exp_dir / "variants" / "baseline"
    if not _dir_nonempty(baseline):
        if baseline.exists():
            shutil.rmtree(baseline)
        _copy_tree(src, baseline)
    ident = _slug_from_name(exp_dir.name)
    yaml_path = exp_dir / "experiment.yaml"
    if not yaml_path.is_file():
        _write_yaml(
            yaml_path,
            {
                "schema_version": SCHEMA_VERSION,
                "id": ident,
                "name": exp_dir.name,
                "artifact": {
                    "type": "dir",
                    "name": ident,
                    "layout": "sidecar",
                    "source_path": str(src.resolve()),
                },
                "criteria": {"path": "criteria.md"},
                "variants": [
                    {
                        "id": "baseline",
                        "role": "baseline",
                        "path": "variants/baseline",
                        "created_by": "import",
                    }
                ],
                "concerns": [
                    {
                        "id": "smoke",
                        "intent": "placeholder objective; replace during briefing",
                        "role": "objective",
                        "measure": {"type": "script", "command": ["true"]},
                    }
                ],
                "matrix": {
                    "cells": [
                        {
                            "id": "local-cli",
                            "command": ["true"],
                            "prompt": {"mode": "stdin"},
                        }
                    ]
                },
                "cases": [
                    {
                        "id": "smoke",
                        "path": "cases/smoke",
                        "prompt_file": "prompt.md",
                    }
                ],
                "isolation": {
                    "type": "tempdir",
                    "subdir": ".",
                    "inherit_host_identity": True,
                },
                "budget": {
                    "max_trials": 24,
                    "max_parallel": 4,
                },
                "repetitions": 3,
            },
        )
    criteria = exp_dir / "criteria.md"
    if not criteria.is_file():
        criteria.write_text("# Criteria\n\nReplace this stub after briefing.\n", encoding="utf-8")
    case_dir = exp_dir / "cases" / "smoke"
    case_dir.mkdir(parents=True, exist_ok=True)
    prompt = case_dir / "prompt.md"
    if not prompt.is_file():
        prompt.write_text("Follow ${program_root}/SKILL.md if present.\n", encoding="utf-8")


def _write_baseline(exp_dir: Path, src: Path | None, source_path: str | None) -> None:
    origin = src or (Path(source_path).expanduser() if source_path else None)
    if origin is None:
        raise ContractError("artifact_missing_dir", "--write-baseline needs --init-from or artifact.source_path")
    origin = origin.resolve()
    if not origin.is_dir():
        raise ContractError("artifact_missing_dir", f"baseline source is not a directory: {origin}")
    dest = exp_dir / "variants" / "baseline"
    if _dir_nonempty(dest):
        raise ContractError(
            "artifact_missing_dir",
            "variants/baseline already exists and is not empty; v1 will not overwrite",
        )
    if dest.exists():
        shutil.rmtree(dest)
    _copy_tree(origin, dest)


def _budget_limits_label(exp) -> str:
    parts: list[str] = []
    if exp.budget.wall_clock_s is not None:
        parts.append(f"experiment {exp.budget.wall_clock_s}s")
    if exp.budget.per_trial.wall_clock_s is not None:
        parts.append(f"per_trial {exp.budget.per_trial.wall_clock_s}s")
    if exp.budget.usd is not None:
        parts.append(f"usd {exp.budget.usd}")
    if exp.budget.tokens is not None:
        parts.append(f"tokens {exp.budget.tokens}")
    return "、".join(parts) if parts else "不设"


def _write_brief_md(path: Path, exp, contract_hash: str, runnable: bool, warnings: list[str], errors: list[str]) -> None:
    concerns = "\n".join(f"  - {c.id} ({c.role}): {c.intent}" for c in exp.concerns)
    cells = []
    for cell in exp.matrix.cells:
        argv = " ".join(cell.command or [f"recipe:{cell.recipe}" if cell.recipe else "(missing)"])
        mode = (cell.prompt.mode if cell.prompt else "stdin")
        cells.append(f"  - {cell.id}: argv=`{argv}`, prompt={mode}")
    cases = ", ".join(c.id for c in exp.cases)
    warns = "\n".join(f"- {w}" for w in warnings) if warnings else "- (none)"
    errs = "\n".join(f"- {e}" for e in errors) if errors else "- (none)"
    path.write_text(
        f"""# Brief: {exp.id}

- RUNNABLE: {'yes' if runnable else 'no'}
- schema_version: {exp.schema_version}
- contract_hash: `{contract_hash}`
- 源 skill: {exp.artifact.source_path or '(unset)'}
- 标准文档: {exp.criteria.path}{' 已确认' if exp.criteria.sha256 else ' 未盖章'}（sha256 见 experiment.yaml；本文只作纪要）
- 关注点:
{concerns}
- 矩阵:
{chr(10).join(cells)}
- 用例: {cases}
- 并行: max_parallel={exp.budget.max_parallel}；墙钟/金额/token 上限: {_budget_limits_label(exp)}
- 隔离: {exp.isolation.type}
- 明确不做: 自动演化、写用户全局 skills

## Errors

{errs}

## Warnings

{warns}
""",
        encoding="utf-8",
    )


def _budget_cap(exp) -> str:
    if exp.budget.usd is not None:
        return str(exp.budget.usd)
    if exp.budget.per_trial.usd is not None:
        return str(trial_count(exp) * exp.budget.per_trial.usd)
    return "n/a"


def _print_summary(*, runnable: bool, exp, warnings: list[str], errors: list[str]) -> None:
    print(f"RUNNABLE: {'yes' if runnable else 'no'}")
    if exp is not None:
        print(f"contract_hash: {fingerprint_contract(exp)}")
        print(f"trials: {trial_count(exp)}")
        print(f"budget_usd_cap: {_budget_cap(exp)}")
    for err in errors:
        print(f"error: {err}")
    for warn in warnings:
        print(f"warning: {warn}")


def _cmd_brief(args: argparse.Namespace) -> int:
    src = Path(args.init_from).expanduser().resolve() if args.init_from else None
    if src is not None and args.exp is None:
        print("brief --init-from requires --exp", file=sys.stderr)
        return 2

    try:
        if args.exp:
            exp_dir = Path(args.exp).expanduser().resolve()
        else:
            exp_dir = resolve_exp_dir(None)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        if src is not None:
            _init_from(exp_dir, src)
        yaml_path = exp_dir / "experiment.yaml"
        if not yaml_path.is_file():
            raise ContractError("unknown_field", f"experiment.yaml not found: {yaml_path}")

        if args.write_baseline:
            raw_for_src = load_raw(exp_dir) if yaml_path.is_file() else {}
            source_path = None
            artifact = raw_for_src.get("artifact") if isinstance(raw_for_src, dict) else None
            if isinstance(artifact, dict):
                source_path = artifact.get("source_path")
            _write_baseline(exp_dir, src, source_path)

        scan_experiment_secrets(exp_dir)

        if args.confirm_criteria:
            exp = load_experiment(exp_dir)
            write_criteria_hash(exp_dir, exp, load_raw(exp_dir))

        exp = load_experiment(exp_dir)
        warnings = validate_experiment(exp, exp_dir, check_criteria_hash=True)
    except ContractError as exc:
        _print_contract_error(exc)
        _print_summary(runnable=False, exp=None, warnings=[], errors=[exc.format_line()])
        return 2
    except Exception as exc:
        print(f"[unknown_field] {exc}", file=sys.stderr)
        _print_summary(runnable=False, exp=None, warnings=[], errors=[str(exc)])
        return 2

    contract_hash = fingerprint_contract(exp)
    _write_brief_md(exp_dir / "brief.md", exp, contract_hash, True, warnings, [])
    _print_summary(runnable=True, exp=exp, warnings=warnings, errors=[])
    preview = preview_cost(exp)
    print(f"disk_preview: {preview.get('disk') or 'n/a'}")
    return 0


def _resolve_exp(args: argparse.Namespace) -> Path:
    return resolve_exp_dir(getattr(args, "exp", None))


def _load_valid(exp_dir: Path):
    scan_experiment_secrets(exp_dir)
    exp = load_experiment(exp_dir)
    validate_experiment(exp, exp_dir, check_criteria_hash=True)
    return exp


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        exp_dir = _resolve_exp(args)
        exp = _load_valid(exp_dir)
        code, _promo, _trials = run_experiment(
            exp,
            exp_dir,
            only_variant=args.only_variant,
            only_cell=args.only_cell,
            only_case=args.only_case,
            keep_sandbox=args.keep_sandbox,
            dry_expand=args.dry_expand,
            gate=args.gate,
            max_parallel=args.max_parallel,
            force=args.force,
        )
        return code
    except ContractError as exc:
        _print_contract_error(exc)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        exp_dir = _resolve_exp(args)
        exp = _load_valid(exp_dir)
        dest = Path(args.o).expanduser().resolve() if args.o else None
        path = write_report(exp, exp_dir, dest)
        print(path)
        if not path.is_file() or path.stat().st_size == 0:
            return 2
        return 0
    except ContractError as exc:
        _print_contract_error(exc)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _cmd_cleanup(args: argparse.Namespace) -> int:
    try:
        exp_dir = _resolve_exp(args)
        exp = _load_valid(exp_dir)
        if exp.isolation.type != "git-worktree" or not exp.isolation.repo:
            print("no leftover worktrees")
            return 0
        repo = resolve_repo(exp.isolation.repo, exp_dir)
        if not repo.exists():
            print("no leftover worktrees")
            return 0
        removed = cleanup_experiment_worktrees(repo, exp_dir)
        if not removed:
            print("no leftover worktrees")
            return 0
        for path in removed:
            print(path)
        return 0
    except ContractError as exc:
        _print_contract_error(exc)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _cmd_promote(args: argparse.Namespace) -> int:
    try:
        exp_dir = _resolve_exp(args)
        exp = _load_valid(exp_dir)
        code, path = promote(
            exp,
            exp_dir,
            only_variant=args.only_variant,
            force=args.force,
            copy=args.copy,
        )
        print(path)
        return code
    except ContractError as exc:
        _print_contract_error(exc)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentlab")
    sub = p.add_subparsers(dest="cmd", required=True)

    brief = sub.add_parser("brief")
    brief.add_argument("--exp")
    brief.add_argument("--init-from")
    brief.add_argument("--write-baseline", action="store_true")
    brief.add_argument("--confirm-criteria", action="store_true")
    brief.set_defaults(func=_cmd_brief)

    run = sub.add_parser("run")
    run.add_argument("--exp")
    run.add_argument("--max-parallel", type=int)
    run.add_argument("--force", action="store_true")
    run.add_argument("--retry-failed", action="store_true")
    run.add_argument("--only-variant")
    run.add_argument("--only-cell")
    run.add_argument("--only-case")
    run.add_argument("--keep-sandbox", action="store_true")
    run.add_argument("--dry-expand", action="store_true")
    run.add_argument("--gate", action="store_true")
    run.set_defaults(func=_cmd_run)

    report = sub.add_parser("report")
    report.add_argument("--exp")
    report.add_argument("--format", dest="fmt", default="md")
    report.add_argument("-o")
    report.set_defaults(func=_cmd_report)

    promote_p = sub.add_parser("promote")
    promote_p.add_argument("--exp")
    promote_p.add_argument("--only-variant", required=True)
    promote_p.add_argument("--force", action="store_true")
    promote_p.add_argument("--copy", action="store_true")
    promote_p.set_defaults(func=_cmd_promote)

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--exp")
    cleanup.set_defaults(func=_cmd_cleanup)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
