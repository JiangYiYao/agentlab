from __future__ import annotations

import os
from pathlib import Path

import yaml

from agentlab.errors import ContractError
from agentlab.schema import Experiment, Recipe


def load_recipe(exp: Experiment, recipe_id: str, experiment_root: Path) -> Recipe:
    if recipe_id in exp.recipes:
        return exp.recipes[recipe_id]
    candidates = [experiment_root / "recipes" / f"{recipe_id}.yaml"]
    home = os.environ.get("AGENTLAB_HOME")
    if home:
        candidates.append(Path(home) / "recipes" / f"{recipe_id}.yaml")
    here = Path(__file__).resolve()
    skill_root = here.parents[2]
    repo_root = here.parents[4] if len(here.parents) > 4 else skill_root
    candidates.append(skill_root / "examples" / "recipes" / f"{recipe_id}.yaml")
    candidates.append(repo_root / "examples" / "recipes" / f"{recipe_id}.yaml")
    for path in candidates:
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ContractError("unknown_recipe", f"recipe {recipe_id!r} is not a mapping")
            data.setdefault("id", recipe_id)
            recipe = Recipe.model_validate(data)
            exp.recipes[recipe_id] = recipe
            return recipe
    raise ContractError("unknown_recipe", f"recipe {recipe_id!r} not found")


def bound_command(exp: Experiment, cell, case, experiment_root: Path) -> tuple[list[str], Recipe | None]:
    recipe = None
    if cell.recipe:
        recipe = load_recipe(exp, cell.recipe, experiment_root)
    if cell.command:
        argv = list(cell.command)
    elif recipe and recipe.command:
        argv = list(recipe.command)
    elif case and case.command:
        argv = list(case.command)
    else:
        raise ContractError("missing_command", f"cell {cell.id} has no command")
    argv.extend(list(cell.args or []))
    return argv, recipe
