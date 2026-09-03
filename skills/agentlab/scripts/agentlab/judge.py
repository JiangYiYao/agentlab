from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.models import Score, Trial
from agentlab.schema import Concern, Experiment
from agentlab.templates import resolve_argv

JUDGE_PREAMBLE = """你是测评裁判，不是被测程序。
工作区快照在当前工作目录。不要修改文件。
只根据下面的标准打分。不要输出分析散文。
最后一行之前不得出现其它 JSON。stdout 必须且只能是一篇 JSON 对象：
{"concern_id":"<id>","value":<number|bool>,"unit":"<string|null>","pass":<true|false>,"soft":<bool>,"unknown":false,"evidence":{}}
"""


def criteria_section(root: Path, concern_id: str) -> str:
    text = (root / "criteria.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lstrip("#").strip() == concern_id:
            start = i
            break
    if start is None:
        return text
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def spawn_judge(trial: Trial, concern: Concern, exp: Experiment, timeout_s: int) -> Score:
    spec = concern.judge or exp.judge
    if spec is None or not spec.command:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": "missing_judge_command"})
    opaque = hashlib.sha256(f"{trial.id}:{concern.id}".encode()).hexdigest()[:12]
    view = trial.experiment_root / "trials" / ".judge" / opaque
    if view.exists():
        shutil.rmtree(view)
    view.mkdir(parents=True, exist_ok=True)
    excerpt = criteria_section(trial.experiment_root, concern.id)
    (view / "criteria-excerpt.md").write_text(excerpt, encoding="utf-8")
    if trial.sandbox:
        snap = view / "workspace"
        if trial.sandbox.project_root.exists():
            shutil.copytree(trial.sandbox.project_root, snap, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    stdin_text = JUDGE_PREAMBLE + "\n## 标准\n" + excerpt + "\n## 工作区\n当前目录即待评快照。\n"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTLAB_")}
    env.pop("AGENTLAB_VARIANT", None)
    argv = resolve_argv(list(spec.command), trial.experiment_root, {"experiment_root": str(trial.experiment_root)})
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(view),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **start_session_kwargs(),
        )
        stdout, stderr = proc.communicate(input=stdin_text.encode(), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if proc is not None and proc.pid:
            kill_process_group(proc.pid)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error_code": "judge_unavailable", "error": "judge timed out"},
        )
    except Exception as exc:
        if proc is not None and proc.pid:
            kill_process_group(proc.pid)
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error_code": "judge_unavailable", "error": str(exc)},
        )
    try:
        payload = json.loads((stdout or b"").decode() or "{}")
        return _score_from_judge(payload, concern.id)
    except Exception:
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error_code": "judge_bad_stdout", "stdout": (stdout or b"")[:300].decode(errors="replace")},
        )


def _score_from_judge(payload: object, concern_id: str) -> Score:
    if not isinstance(payload, dict):
        raise ValueError("judge stdout is not a JSON object")
    if "pass" in payload and payload["pass"] is not None and not isinstance(payload["pass"], bool):
        raise ValueError("pass must be boolean")
    if "unknown" in payload and not isinstance(payload["unknown"], bool):
        raise ValueError("unknown must be boolean")
    score = Score.from_json(payload)
    score.concern_id = concern_id
    return score
