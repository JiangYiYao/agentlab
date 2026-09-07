from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.models import Score, Trial
from agentlab.schema import Concern, Experiment
from agentlab.templates import resolve_argv

JUDGE_PREAMBLE = """你是测评裁判，不是被测程序。
当前工作目录就是待评代码根。不要修改文件。
只评下面标明的这一次试验、这一个关注点。任务说明和标准写了评什么，就评什么。
不要输出分析散文。stdout 里给出一篇 JSON 对象（前后可以有日志；不要用 markdown 围栏）：
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


def criteria_for_judge(root: Path, concern_id: str) -> str:
    text = (root / "criteria.md").read_text(encoding="utf-8")
    section = criteria_section(root, concern_id)
    if section.strip() == text.strip():
        return text
    lines = text.splitlines()
    first_h2 = next((i for i, line in enumerate(lines) if line.startswith("## ")), None)
    preface = "\n".join(lines[:first_h2]).strip() if first_h2 is not None else ""
    if preface:
        return preface + "\n\n" + section
    return section


def extract_json_payload(text: str) -> object:
    candidates = [text, _strip_fences(text)]
    last: Exception | None = None
    for raw in candidates:
        start = raw.find("{")
        if start < 0:
            continue
        try:
            payload, _end = json.JSONDecoder().raw_decode(raw[start:])
            return payload
        except json.JSONDecodeError as exc:
            last = exc
    raise last or ValueError("no json object")


def spawn_judge(trial: Trial, concern: Concern, exp: Experiment, timeout_s: int) -> Score:
    spec = concern.judge or exp.judge
    if spec is None or not spec.command:
        return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": "missing_judge_command"})
    opaque = hashlib.sha256(f"{trial.id}:{concern.id}".encode()).hexdigest()[:12]
    view = trial.experiment_root / "trials" / ".judge" / opaque
    if view.exists():
        shutil.rmtree(view)
    view.mkdir(parents=True, exist_ok=True)
    excerpt = criteria_for_judge(trial.experiment_root, concern.id)
    prompt = _case_prompt(trial)
    (view / "criteria-excerpt.md").write_text(excerpt, encoding="utf-8")
    criteria_src = trial.experiment_root / "criteria.md"
    if criteria_src.is_file():
        shutil.copy2(criteria_src, view / "criteria.md")
    if prompt:
        (view / "prompt.md").write_text(prompt, encoding="utf-8")
    (view / "trial.json").write_text(
        json.dumps(
            {
                "trial_id": trial.id,
                "variant_id": trial.variant.id,
                "cell_id": trial.cell.id,
                "case_id": trial.case.id,
                "concern_id": concern.id,
                "repeat": trial.repeat,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace = view / "workspace"
    if trial.sandbox and trial.sandbox.project_root.exists():
        shutil.copytree(trial.sandbox.project_root, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    change_summary = _change_summary(trial)
    if change_summary:
        (view / "changes.txt").write_text(change_summary + "\n", encoding="utf-8")
    diff_src = trial.outputs_dir() / "workspace.diff"
    if diff_src.is_file():
        shutil.copy2(diff_src, view / "changes.diff")
    stdin_text = _judge_stdin(trial, concern, excerpt, prompt, change_summary)
    (view / "stdin.md").write_text(stdin_text, encoding="utf-8")
    cwd = workspace if workspace.is_dir() else view
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTLAB_")}
    env.pop("AGENTLAB_VARIANT", None)
    argv = resolve_argv(list(spec.command), trial.experiment_root, {"experiment_root": str(trial.experiment_root)})
    proc: subprocess.Popen | None = None
    stdout = stderr = b""
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **start_session_kwargs(),
        )
        stdout, stderr = proc.communicate(input=stdin_text.encode(), timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or stdout
        stderr = exc.stderr or stderr
        if proc is not None and proc.pid:
            kill_process_group(proc.pid)
            try:
                more_out, more_err = proc.communicate(timeout=5)
                stdout = stdout or more_out
                stderr = stderr or more_err
            except subprocess.TimeoutExpired:
                pass
        _write_judge_logs(view, stdout, stderr)
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error_code": "judge_unavailable", "error": "judge timed out"},
        )
    except Exception as exc:
        if proc is not None and proc.pid:
            kill_process_group(proc.pid)
        _write_judge_logs(view, stdout, stderr)
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error_code": "judge_unavailable", "error": str(exc)},
        )
    stdout_text = (stdout or b"").decode(errors="replace")
    stderr_text = (stderr or b"").decode(errors="replace")
    _write_judge_logs(view, stdout, stderr)
    try:
        payload = extract_json_payload(stdout_text)
        return _score_from_judge(payload, concern.id)
    except Exception:
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={
                "error_code": "judge_bad_stdout",
                "stdout": stdout_text[:300],
                "stdout_log": str(view / "stdout.log"),
                "stderr_log": str(view / "stderr.log"),
            },
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


def _strip_fences(text: str) -> str:
    s = text.strip()
    start = s.find("```")
    if start < 0:
        return s
    rest = s[start + 3 :]
    if rest.lower().startswith("json"):
        rest = rest[4:]
    rest = rest.lstrip("\n")
    end = rest.find("```")
    if end >= 0:
        return rest[:end].strip()
    return rest.strip()


def _case_prompt(trial: Trial) -> str:
    case_dir = trial.experiment_root / (trial.case.path or f"cases/{trial.case.id}")
    path = case_dir / (trial.case.prompt_file or "prompt.md")
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def _change_summary(trial: Trial) -> str:
    diff_json = trial.outputs_dir() / "diff.json"
    if diff_json.is_file():
        try:
            data = json.loads(diff_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            rows = []
            for item in data.get("paths") or []:
                if not isinstance(item, dict):
                    continue
                status = item.get("status") or "?"
                path = item.get("path") or ""
                src = item.get("from")
                rows.append(f"{status} {path}" + (f" <- {src}" if src else ""))
            if rows:
                return "\n".join(rows)
            return "（无文件改动）"
    return ""


def _judge_stdin(trial: Trial, concern: Concern, excerpt: str, prompt: str, change_summary: str) -> str:
    parts = [
        JUDGE_PREAMBLE,
        "",
        "## 这次试验",
        f"trial_id: {trial.id}",
        f"variant_id: {trial.variant.id}",
        f"cell_id: {trial.cell.id}",
        f"case_id: {trial.case.id}",
        f"concern_id: {concern.id}",
        "只评上面标明的这次试验和这个关注点。以任务说明为准。",
        "",
    ]
    if prompt.strip():
        parts += ["## 任务说明", prompt.strip(), ""]
    parts += ["## 标准", excerpt.strip(), ""]
    if change_summary.strip():
        parts += ["## 改动摘要", change_summary.strip(), ""]
    parts += [
        "## 工作区",
        "当前工作目录就是待评代码根。",
        "上级目录有 criteria.md（全文）、prompt.md、trial.json；若有改动补丁则是 changes.diff。",
        "不要修改这些文件。",
        "",
    ]
    return "\n".join(parts)


def _write_judge_logs(view: Path, stdout: bytes | None, stderr: bytes | None) -> None:
    try:
        (view / "stdout.log").write_bytes(stdout or b"")
        (view / "stderr.log").write_bytes(stderr or b"")
    except OSError:
        pass
