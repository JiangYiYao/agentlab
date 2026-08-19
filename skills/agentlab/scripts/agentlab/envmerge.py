from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from agentlab.errors import ContractError
from agentlab.schema import IDENTITY_ENV, RECIPE_ENV_ALLOW
from agentlab.templates import expand_templates


def inherit_flag(cell_flag: bool | None, recipe_flag: bool | None, isolation_flag: bool) -> bool:
    if cell_flag is not None:
        return cell_flag
    if recipe_flag is not None:
        return recipe_flag
    return isolation_flag


def merge_env(
    *,
    overlays: Mapping[str, str],
    recipe_env: Mapping[str, str] | None = None,
    cell_env: Mapping[str, str] | None = None,
    case_env: Mapping[str, str] | None = None,
    ctx: Mapping[str, str] | None = None,
    inherit_home: bool = True,
    sandbox_home: Path | None = None,
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    ctx_map = dict(ctx or {})
    for key, value in overlays.items():
        env[key] = expand_templates(value, ctx_map) if "${" in value else value
    if recipe_env:
        extra = set(recipe_env) - RECIPE_ENV_ALLOW
        if extra:
            raise ContractError("recipe_env_not_allowed", f"recipe.env extra keys {sorted(extra)}")
        for key, value in recipe_env.items():
            env[key] = expand_templates(value, ctx_map) if "${" in value else value
    for extra_env in (cell_env, case_env):
        if not extra_env:
            continue
        for key, value in extra_env.items():
            env[key] = expand_templates(value, ctx_map) if "${" in value else value
    if not inherit_home:
        if sandbox_home is None:
            raise ContractError("unknown_field", "inherit_host_identity=false requires sandbox.home")
        env["HOME"] = str(sandbox_home)
    return env


def isolation_overlays(
    *,
    experiment_root: Path,
    project_root: Path,
    trial_out: Path,
    program_root: Path,
    case_path: Path,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    out = {
        "AGENTLAB_EXPERIMENT_ROOT": str(experiment_root),
        "AGENTLAB_PROJECT_ROOT": str(project_root),
        "AGENTLAB_TRIAL_OUT": str(trial_out),
        "AGENTLAB_PROGRAM_ROOT": str(program_root),
        "AGENTLAB_CASE_DIR": str(case_path),
    }
    if extra:
        out.update(extra)
    return out
