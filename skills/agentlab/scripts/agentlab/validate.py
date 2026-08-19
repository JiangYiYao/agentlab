from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentlab.errors import ContractError
from agentlab.schema import Experiment, Recipe, trial_count

TEMPLATE = re.compile(r"\$\{([^}]+)\}")
KNOWN_VARS = {
    "case.id",
    "variant.id",
    "cell.id",
    "trial.id",
    "artifact.name",
    "cell.model",
    "case.path",
    "experiment_root",
    "sandbox",
    "project_root",
    "trial_out",
    "program_root",
    "report_path",
    "installed_skill",
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_raw(root: Path) -> dict[str, Any]:
    text = (root / "experiment.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ContractError("unknown_field", "experiment.yaml must be a mapping")
    return data


def load_experiment(root: Path) -> Experiment:
    return parse_experiment(load_raw(root))


def _recipe_search_paths(root: Path, recipe_id: str) -> list[Path]:
    paths = [root / "recipes" / f"{recipe_id}.yaml"]
    home = os.environ.get("AGENTLAB_HOME")
    if home:
        paths.append(Path(home) / "recipes" / f"{recipe_id}.yaml")
    return paths


def _hydrate_recipes(exp: Experiment, root: Path) -> None:
    needed = {cell.recipe for cell in exp.matrix.cells if cell.recipe and cell.recipe not in exp.recipes}
    for recipe_id in needed:
        for path in _recipe_search_paths(root, recipe_id):
            if not path.is_file():
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ContractError("unknown_recipe", f"recipe {recipe_id!r} is not a mapping", path=recipe_id)
            data.setdefault("id", recipe_id)
            exp.recipes[recipe_id] = Recipe.model_validate(data)
            break


def parse_experiment(data: dict[str, Any]) -> Experiment:
    try:
        return Experiment.model_validate(data)
    except ContractError:
        raise
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            typ = err.get("type", "")
            msg = err.get("msg", str(exc))
            if typ == "extra_forbidden":
                raise ContractError("unknown_field", msg, path=loc) from exc
            if "artifact.type" in loc:
                raise ContractError("unsupported_artifact_type", msg, path=loc) from exc
        raise ContractError("unknown_field", str(exc)) from exc


def _walk_strings(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk_strings(item))
    elif isinstance(obj, dict):
        for item in obj.values():
            found.extend(_walk_strings(item))
    return found


def validate_experiment(exp: Experiment, root: Path, *, check_criteria_hash: bool = True) -> list[str]:
    warnings: list[str] = []
    ids: dict[str, str] = {}

    def claim(kind: str, ident: str, path: str) -> None:
        key = f"{kind}:{ident}"
        if key in ids:
            raise ContractError("duplicate_id", f"duplicate {kind} id {ident!r}", path=path)
        ids[key] = path

    claim("experiment", exp.id, "id")
    baselines = [v for v in exp.variants if v.role == "baseline"]
    treatments = [v for v in exp.variants if v.role == "treatment"]
    if len(baselines) != 1:
        raise ContractError("need_exactly_one_baseline", f"need exactly one baseline, got {len(baselines)}")
    if len(treatments) > 3:
        raise ContractError("too_many_treatments", f"treatments={len(treatments)}")
    if not (1 <= len(exp.concerns) <= 8):
        raise ContractError("too_many_concerns", f"concerns={len(exp.concerns)}")
    if not exp.matrix.cells:
        raise ContractError("no_matrix_cells", "matrix.cells is empty")
    if not exp.cases:
        raise ContractError("no_cases", "cases is empty")
    n_trials = trial_count(exp)
    if n_trials > exp.budget.max_trials:
        raise ContractError(
            "too_many_trials",
            f"expanded trials={n_trials} exceeds budget.max_trials={exp.budget.max_trials}",
        )

    for variant in exp.variants:
        claim("variant", variant.id, f"variants.{variant.id}")
        variant_dir = (root / variant.path).resolve()
        if not variant_dir.is_dir() or not any(variant_dir.iterdir()):
            raise ContractError("artifact_missing_dir", f"{variant.path} is not a non-empty directory", path=variant.path)
        if variant.role == "baseline" and variant.hypothesis not in (None,):
            raise ContractError("baseline_must_not_hypothesize", "baseline must not have hypothesis")
        if variant.role == "treatment" and variant.hypothesis is None:
            raise ContractError("treatment_missing_hypothesis", "treatment missing hypothesis", path=variant.id)

    if exp.isolation.type == "git-worktree" and not exp.isolation.repo:
        raise ContractError("worktree_missing_repo", "git-worktree requires isolation.repo")

    if exp.budget.usd is not None and exp.budget.per_trial.usd is None:
        raise ContractError("budget_reserve_required", "budget.usd requires per_trial.usd")
    if exp.budget.tokens is not None and exp.budget.per_trial.tokens is None:
        raise ContractError("budget_reserve_required", "budget.tokens requires per_trial.tokens")

    promo = exp.promotion
    if exp.matrix.all_cells_must_pass is not None and exp.matrix.all_cells_must_pass != promo.all_cells_must_pass:
        raise ContractError("all_cells_flag_conflict", "matrix.all_cells_must_pass conflicts with promotion")
    if promo.all_cells_must_pass and exp.matrix.cell_rule:
        raise ContractError("cell_rule_while_all_must_pass", "cell_rule illegal while all_cells_must_pass")
    if not promo.all_cells_must_pass:
        rule = exp.matrix.cell_rule or {}
        if rule.get("type") != "named_cells" or not rule.get("require"):
            raise ContractError("no_average_cell_pass", "need cell_rule.type=named_cells")
        cell_ids = {c.id for c in exp.matrix.cells}
        for cid in rule.get("require", []):
            if cid not in cell_ids:
                raise ContractError("unknown_cell_in_rule", f"unknown cell {cid!r} in cell_rule.require")

    concern_ids = {c.id for c in exp.concerns}
    for cid in promo.accept_soft_gates:
        if cid not in concern_ids:
            raise ContractError("unknown_concern_in_accept", f"unknown concern {cid!r} in accept_soft_gates")

    _hydrate_recipes(exp, root)
    for cell in exp.matrix.cells:
        claim("cell", cell.id, f"matrix.cells.{cell.id}")
        if cell.recipe and cell.recipe not in exp.recipes:
            raise ContractError("unknown_recipe", f"recipe {cell.recipe!r} not found", path=cell.id)

    for case in exp.cases:
        claim("case", case.id, f"cases.{case.id}")

    for concern in exp.concerns:
        claim("concern", concern.id, f"concerns.{concern.id}")
        if concern.role == "gate" and concern.scope is None:
            raise ContractError("gate_missing_scope", "gate requires scope", path=concern.id)
        if concern.role == "gate" and concern.pass_ is None:
            raise ContractError("gate_missing_pass", "gate requires pass", path=concern.id)
        if concern.role == "gate" and concern.soft and concern.id not in promo.accept_soft_gates:
            raise ContractError("soft_measure_cannot_be_gate", "soft gate not accepted", path=concern.id)
        if concern.role == "gate" and concern.measure.type == "llm_rubric" and concern.id not in promo.accept_soft_gates:
            raise ContractError("llm_cannot_be_gate", "llm_rubric cannot be gate", path=concern.id)
        if concern.role == "gate" and concern.measure.extractor == "llm":
            raise ContractError("llm_extract_cannot_be_gate", "label_extract extractor=llm cannot be gate", path=concern.id)
        if concern.role == "gate":
            agg = concern.aggregate or "all_pass"
            if agg != "all_pass":
                raise ContractError("gate_must_all_pass", "gate aggregate must be all_pass", path=concern.id)
        if concern.measure.type == "must_list" and concern.measure.mode not in (None, "paths_or_substrings"):
            raise ContractError("must_list_mode_unsupported", "must_list.mode must be paths_or_substrings")
        if concern.measure.type == "must_list" and not (concern.measure.keep or concern.measure.gone):
            raise ContractError("unknown_field", "must_list needs keep or gone", path=concern.id)
        if concern.measure.type == "gold_tree":
            gold = concern.measure.gold_dir
            if gold and "${" not in gold and not (root / gold).exists():
                raise ContractError("missing_gold", f"gold_dir missing: {gold}", path=concern.id)
        if concern.measure.type == "llm_rubric":
            has_judge = (concern.judge and concern.judge.command) or (exp.judge and exp.judge.command)
            if not has_judge:
                raise ContractError("missing_judge_command", "llm_rubric needs judge.command", path=concern.id)
        if concern.pass_ and concern.measure.type in {
            "gold_tree",
            "must_list",
            "label_extract",
            "section_present",
            "no_upgrade",
            "path_under",
            "counterarg_inline",
            "workspace_diff",
        }:
            if concern.pass_.op not in {"==", "!="}:
                raise ContractError("bool_op_not_eq", "bool measure pass.op must be == or !=", path=concern.id)

        raw_measure = concern.measure.model_dump()
        for text in _walk_strings(raw_measure) + _walk_strings(cell_commands(exp)):
            _check_templates(text, exp)

    for cell in exp.matrix.cells:
        argv = resolve_command(exp, cell, exp.cases[0] if exp.cases else None)
        if not argv:
            raise ContractError("missing_command", f"cell {cell.id} has no command", path=cell.id)
        joined = " ".join(argv)
        if "${cell.model}" in joined and not cell.model:
            raise ContractError("model_unbound", f"cell {cell.id} uses ${{cell.model}} but model is unset", path=cell.id)
        _check_templates(joined, exp)
        bin0 = argv[0]
        if "${" not in bin0 and "/" not in bin0 and not bin0.startswith("."):
            if shutil.which(bin0) is None:
                raise ContractError("bin_not_on_path", f"{bin0!r} not on PATH", path=cell.id)
        for arg in argv[1:]:
            if "${" in arg:
                continue
            if ("/" in arg or arg.startswith(".")) and not Path(arg).is_absolute():
                cand = (root / arg).resolve()
                if not cand.exists():
                    warnings.append(
                        f"relative argv {arg!r} does not exist under experiment root; left unchanged"
                    )

    criteria_path = root / exp.criteria.path
    if not criteria_path.is_file():
        raise ContractError("missing_criteria", f"missing {exp.criteria.path}")
    digest = _sha256_file(criteria_path)
    if check_criteria_hash:
        if not exp.criteria.sha256:
            raise ContractError("missing_criteria", "criteria.sha256 missing; run brief --confirm-criteria")
        if exp.criteria.sha256 != digest:
            raise ContractError("criteria_hash_mismatch", "criteria.md hash does not match yaml")

    return warnings


def cell_commands(exp: Experiment) -> list[list[str]]:
    out: list[list[str]] = []
    for cell in exp.matrix.cells:
        if cell.command:
            out.append(cell.command)
        elif cell.recipe and cell.recipe in exp.recipes and exp.recipes[cell.recipe].command:
            out.append(exp.recipes[cell.recipe].command or [])
        else:
            for case in exp.cases:
                if case.command:
                    out.append(case.command)
    return out


def resolve_command(exp: Experiment, cell, case) -> list[str] | None:
    if cell.command:
        return list(cell.command) + list(cell.args or [])
    if cell.recipe and cell.recipe in exp.recipes and exp.recipes[cell.recipe].command:
        return list(exp.recipes[cell.recipe].command or []) + list(cell.args or [])
    if case and case.command:
        return list(case.command) + list(cell.args or [])
    return None


def _check_templates(text: str, exp: Experiment) -> None:
    for match in TEMPLATE.finditer(text):
        name = match.group(1)
        if name == "cell.model":
            continue
        if name not in KNOWN_VARS:
            raise ContractError("unknown_template_var", f"unknown template ${{{name}}}")


def write_criteria_hash(root: Path, exp: Experiment, data: dict[str, Any]) -> str:
    digest = _sha256_file(root / exp.criteria.path)
    data.setdefault("criteria", {})
    if not isinstance(data["criteria"], dict):
        data["criteria"] = {"path": "criteria.md"}
    data["criteria"]["sha256"] = digest
    data["criteria"].setdefault("path", exp.criteria.path)
    dump = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    (root / "experiment.yaml").write_text(dump, encoding="utf-8")
    return digest
