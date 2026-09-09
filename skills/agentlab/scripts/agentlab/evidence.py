"""Portable, bounded evidence shared by script and language-model evaluators."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from agentlab.models import Trial
from agentlab.provenance import atomic_json
from agentlab.schema import Experiment


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def capture_evidence(trial: Trial, exp: Experiment) -> Path:
    out = trial.outputs_dir()
    dest = out / "evidence"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    entries = []
    # Record athlete-owned outputs before any evaluator can create or replace them.
    # Relative paths survive copying the trial into a historical run archive.
    execution_files = {
        src.relative_to(out).as_posix(): file_digest(src)
        for src in out.rglob("*")
        if src.is_file() and not src.is_relative_to(dest)
        and src.resolve().is_relative_to(out.resolve())
    }

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
            (dest / "workspace").mkdir(parents=True, exist_ok=True)
            for src in project.rglob("*"):
                if src.is_dir() and ".git" not in src.relative_to(project).parts and src.resolve().is_relative_to(project.resolve()):
                    (dest / "workspace" / src.relative_to(project)).mkdir(parents=True, exist_ok=True)
                if src.is_file() and ".git" not in src.relative_to(project).parts and src.resolve().is_relative_to(project.resolve()):
                    copy(src, Path("workspace") / src.relative_to(project))
    atomic_json(dest / "manifest.json", {"execution_id": trial.execution_id, "entries": entries,
                                        "execution_files": execution_files})
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


def describe_materials(evidence: Path, prefix: str, *, patch: Path | None = None,
                       patch_label: str | None = None, after_label: str | None = None,
                       workspace_label: str | None = None) -> list[str]:
    """Describe available artifacts without assuming the task produces code."""
    lines = [f"证据清单：{prefix}/manifest.json。先核对材料是否缺失或截断，按任务和评价标准选择相关内容。"]
    stdout = evidence / "stdout.log"
    if stdout.is_file() and stdout.stat().st_size:
        lines.append(f"文本回答或执行日志：{prefix}/stdout.log。根据任务区分回答和日志。")
    files = evidence / "files"
    if files.is_dir() and any(p.is_file() for p in files.rglob("*")):
        lines.append(f"声明的产出文件：{prefix}/files/。文件和结构化数据任务应检查对应文件的实际内容。")
    if patch and patch.is_file() and patch.stat().st_size:
        lines.append(f"文件改动：{patch_label}。任务涉及修改文件时，结合任务要求审查这些差异。")
        if after_label:
            lines.append(f"改后文件：{after_label}。需要上下文时查看对应文件。")
    if workspace_label:
        lines.append(f"工作区材料：{workspace_label}。仅在任务需要时查看相关文件。")
    lines.append("没有代码改动的任务无需补丁；缺少任务必需的证据时标记 unknown，并说明缺失内容。")
    return lines
