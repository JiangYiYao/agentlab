"""Portable, bounded evidence shared by script and language-model evaluators."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from agentlab.models import Trial
from agentlab.provenance import atomic_json
from agentlab.schema import Experiment


def capture_evidence(trial: Trial, exp: Experiment) -> Path:
    out = trial.outputs_dir()
    dest = out / "evidence"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    entries = []

    def copy(src: Path, rel: Path):
        item = {"path": rel.as_posix(), "source": str(src), "missing": not src.is_file(), "truncated": False}
        if src.is_file():
            size = src.stat().st_size
            item["size"] = size
            item["truncated"] = size > exp.evidence.max_file_bytes
            with src.open("rb") as stream:
                data = stream.read(exp.evidence.max_file_bytes)
            item["sha256"] = hashlib.sha256(data).hexdigest()
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        entries.append(item)

    for name in ("stdout.log", "stderr.log", "prompt.md", "usage.json", "workspace.diff"):
        copy(out / name, Path(name))
    for pattern in exp.evidence.files:
        matches = [p for p in out.glob(pattern) if p.is_file() and "evidence" not in p.relative_to(out).parts]
        if not matches:
            entries.append({"path": pattern, "missing": True, "truncated": False})
        for src in matches:
            if src.resolve().is_relative_to(out.resolve()):
                copy(src, Path("files") / src.relative_to(out))
    if trial.sandbox and trial.sandbox.project_root.is_dir():
        if exp.evidence.workspace or (any(c.measure.type == "llm_rubric" for c in exp.concerns) and (exp.judge is None or exp.judge.mode != "compare_case")):
            project = trial.sandbox.project_root
            for src in project.rglob("*"):
                if src.is_file() and ".git" not in src.relative_to(project).parts and src.resolve().is_relative_to(project.resolve()):
                    copy(src, Path("workspace") / src.relative_to(project))
    atomic_json(dest / "manifest.json", {"execution_id": trial.execution_id, "entries": entries})
    return dest


def copy_evidence(trial: Trial, dest: Path) -> None:
    source = trial.outputs_dir() / "evidence"
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True)
    else:
        # Direct adapter use and legacy runs still expose text evidence.
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("stdout.log", "stderr.log", "prompt.md", "usage.json"):
            src = trial.outputs_dir() / name
            if src.is_file():
                shutil.copy2(src, dest / name)
