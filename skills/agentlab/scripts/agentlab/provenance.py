"""Content identities for execution and measurement; decisions are not cached here."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from agentlab.schema import Experiment, Concern
from agentlab.models import Trial


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tree_digest(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_file():
        return digest([hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777])
    items = []
    for file in sorted(path.rglob("*")):
        if ".git" in file.relative_to(path).parts or "__pycache__" in file.parts:
            continue
        if file.is_file():
            items.append((file.relative_to(path).as_posix(), tree_digest(file)))
    return digest(items)


def dependency_hashes(root: Path, specs: list[str]) -> dict[str, str]:
    out = {}
    for spec in specs:
        if "${" in spec:
            spec = spec.replace("${experiment_root}", str(root))
        path = Path(spec).expanduser()
        if not path.is_absolute():
            path = root / path
        out[str(path)] = tree_digest(path)
    return out


def command_files(root: Path, command: list[str]) -> dict[str, str]:
    files = []
    for i, arg in enumerate(command):
        arg = arg.replace("${experiment_root}", str(root))
        if i == 0:
            arg = shutil.which(arg) or arg
        path = Path(arg)
        try:
            if (i == 0 or path.suffix in {".py", ".sh", ".js", ".mjs", ".rb", ".pl"}) and (root / path).is_file():
                files.append(str(path))
        except OSError:
            pass
    return dependency_hashes(root, files)


def execution_basis(exp: Experiment, trial: Trial) -> str:
    from agentlab.recipes import bound_command
    root = trial.experiment_root
    command, recipe = bound_command(exp, trial.cell, trial.case, root)
    case = trial.case.model_dump(mode="json", exclude={"expected_labels", "require_exit_0"})
    prompt = root / (trial.case.path or f"cases/{trial.case.id}") / trial.case.prompt_file
    return digest({
        "engine": 2,
        "variant": tree_digest(root / trial.variant.path),
        "artifact": exp.artifact.model_dump(mode="json"),
        "case": case, "prompt": tree_digest(prompt),
        "inputs": dependency_hashes(root, trial.case.inputs),
        "cell": trial.cell.model_dump(mode="json"),
        "recipe": recipe.model_dump(mode="json") if recipe else None,
        "command_files": command_files(root, command),
        "isolation": exp.isolation.model_dump(mode="json", exclude={"keep_sandbox", "keep_on_fail", "protected_paths"}),
        "limits": exp.budget.per_trial.model_dump(mode="json"),
        "evidence": exp.evidence.model_dump(mode="json"),
    })


def measurement_basis(exp: Experiment, trial: Trial, concern: Concern) -> str:
    root = trial.experiment_root
    measure = concern.measure
    refs = list(measure.inputs)
    for spec in (measure.keep, measure.gone, measure.gold_dir, measure.source):
        if spec and "${trial_out}" not in spec and "${project_root}" not in spec and "${report_path}" not in spec:
            refs.append(spec)
    data = {
        "engine": 2, "execution": trial.execution_basis,
        "measure": measure.model_dump(mode="json"),
        "dependencies": dependency_hashes(root, refs),
        "command_files": command_files(root, measure.command or []),
        "expected": trial.case.expected_labels,
        "expected_file": tree_digest(root / (trial.case.path or f"cases/{trial.case.id}") / "expected_labels.yaml"),
    }
    if measure.type == "llm_rubric":
        spec = concern.judge or exp.judge
        data.update(criteria=tree_digest(root / exp.criteria.path), intent=concern.intent,
                    judge=spec.model_dump(mode="json") if spec else None,
                    judge_files=command_files(root, spec.command if spec else []))
    return digest(data)


def atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)
