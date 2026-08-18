from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from agentlab.errors import ContractError
from agentlab.schema import Experiment
from agentlab.validate import KNOWN_VARS, TEMPLATE

RECIPE_ENV_ALLOW = {"CODEX_HOME", "CLAUDE_CONFIG_DIR"}


def looks_like_relpath(arg: str) -> bool:
    return "/" in arg or arg.startswith("./") or arg.startswith("../")


def host_scutio_python() -> str:
    cfg = _agentlab_config()
    if cfg.get("scutio_python"):
        return str(cfg["scutio_python"])
    env = os.environ.get("SCUTIO_PYTHON")
    if env:
        return env
    home = os.environ.get("SCUTIO_HOME", str(Path.home() / ".scutio"))
    return str(Path(home) / ".venv" / "bin" / "python")


def host_scutio_toolkit_scripts() -> str:
    cfg = _agentlab_config()
    if cfg.get("scutio_toolkit_scripts"):
        return str(cfg["scutio_toolkit_scripts"])
    py = Path(host_scutio_python())
    inferred = py.parent.parent.parent / "skills" / "scutio-toolkit" / "scripts"
    return str(inferred)


def _agentlab_config() -> dict[str, Any]:
    home = Path(os.environ.get("AGENTLAB_HOME", Path.home() / ".agentlab"))
    path = home / "config.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}


def build_context(
    *,
    exp: Experiment,
    experiment_root: Path,
    variant_id: str | None = None,
    cell_id: str | None = None,
    case_id: str | None = None,
    trial_id: str | None = None,
    cell_model: str | None = None,
    case_path: str | None = None,
    sandbox: Path | None = None,
    project_root: Path | None = None,
    trial_out: Path | None = None,
    program_root: Path | None = None,
    replay: dict[str, Any] | None = None,
) -> dict[str, str]:
    ctx: dict[str, str] = {
        "artifact.name": exp.artifact.name,
        "experiment_root": str(experiment_root),
        "host.scutio_python": host_scutio_python(),
        "host.scutio_toolkit_scripts": host_scutio_toolkit_scripts(),
        "report_path": "${report_path}",
    }
    if variant_id:
        ctx["variant.id"] = variant_id
    if cell_id:
        ctx["cell.id"] = cell_id
    if case_id:
        ctx["case.id"] = case_id
    if trial_id:
        ctx["trial.id"] = trial_id
    if cell_model:
        ctx["cell.model"] = cell_model
    if case_path:
        ctx["case.path"] = case_path
    if sandbox:
        ctx["sandbox"] = str(sandbox)
    if project_root:
        ctx["project_root"] = str(project_root)
    if trial_out:
        ctx["trial_out"] = str(trial_out)
    if program_root:
        ctx["program_root"] = str(program_root)
        ctx["installed_skill"] = str(program_root)
    if replay:
        lock = replay.get("lock") or {}
        for key in ("code", "name", "as_of", "horizon", "market"):
            if lock.get(key) is not None:
                ctx[f"replay.{key}"] = str(lock[key])
        if replay.get("clock"):
            ctx["case.replay.clock"] = str(replay["clock"])
    return ctx


def expand_templates(text: str, ctx: dict[str, str], *, allow_unbound_model: bool = False) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "report_path":
            return "${report_path}"
        if name == "cell.model" and name not in ctx:
            if allow_unbound_model:
                return match.group(0)
            raise ContractError("model_unbound", "cell.model used but model is unset")
        if name not in ctx:
            if name not in KNOWN_VARS:
                raise ContractError("unknown_template_var", f"unknown template ${{{name}}}")
            raise ContractError("unknown_template_var", f"unbound template ${{{name}}}")
        return ctx[name]

    return TEMPLATE.sub(repl, text)


def resolve_argv(argv: list[str], experiment_root: Path, ctx: dict[str, str]) -> list[str]:
    expanded = [expand_templates(x, ctx) for x in argv]
    if not expanded:
        return []
    bin0, rest = expanded[0], expanded[1:]
    if "/" not in bin0 and not bin0.startswith("."):
        found = shutil.which(bin0)
        if found is None:
            raise ContractError("bin_not_on_path", f"{bin0!r} not on PATH")
        out = [found]
    else:
        out = [str((experiment_root / bin0).resolve()) if not Path(bin0).is_absolute() else bin0]
    for arg in rest:
        if looks_like_relpath(arg) and not Path(arg).is_absolute():
            cand = (experiment_root / arg).resolve()
            out.append(str(cand) if cand.exists() else arg)
        else:
            out.append(arg)
    return out
