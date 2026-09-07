from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.judge import (
    criteria_for_judge,
    extract_json_payload,
    _case_prompt,
    _score_from_judge,
    _write_judge_logs,
)
from agentlab.models import Score, Trial
from agentlab.runs import archive_trial, runs_dir
from agentlab.schema import Concern, Experiment, judge_mode
from agentlab.templates import resolve_argv

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def run_compare_judges(exp: Experiment, root: Path, trials: list[Trial], run_id: str | None) -> list[Path]:
    if judge_mode(exp) != "compare_case":
        return []
    concerns = [c for c in exp.concerns if c.measure.type == "llm_rubric"]
    if not concerns:
        return []
    groups: dict[tuple[str, str, int], list[Trial]] = {}
    for trial in trials:
        if trial.skipped:
            continue
        groups.setdefault((trial.cell.id, trial.case.id, trial.repeat), []).append(trial)
    written: list[Path] = []
    for (cell_id, case_id, repeat), group in sorted(groups.items()):
        dest = _compare_dir(root, run_id, cell_id, case_id, repeat)
        result = spawn_compare(exp, root, group, concerns, dest)
        _apply_compare_scores(group, concerns, result)
        for trial in group:
            _write_trial_scores(trial)
            if run_id:
                archive_trial(root, run_id, trial.id, reused_from=trial.reused_from if trial.reused else None)
        written.append(dest / "result.json")
    return written


def spawn_compare(
    exp: Experiment,
    root: Path,
    group: list[Trial],
    concerns: list[Concern],
    dest: Path,
) -> dict[str, Any]:
    spec = exp.judge or next((c.judge for c in concerns if c.judge and c.judge.command), None)
    mapping = _blind_mapping([t.variant.id for t in group], dest.name)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    by_variant = {t.variant.id: t for t in group}
    for label, vid in mapping.items():
        trial = by_variant[vid]
        _pack_label(dest, label, trial)
    prompt = _case_prompt(group[0])
    if prompt:
        (dest / "prompt.md").write_text(prompt, encoding="utf-8")
    criteria_src = root / "criteria.md"
    if criteria_src.is_file():
        shutil.copy2(criteria_src, dest / "criteria.md")
    excerpt = _compare_excerpt(root, concerns)
    (dest / "criteria-excerpt.md").write_text(excerpt, encoding="utf-8")
    stdin_text = _compare_stdin(group[0], concerns, mapping, excerpt, prompt)
    (dest / "stdin.md").write_text(stdin_text, encoding="utf-8")
    payload: dict[str, Any] = {
        "cell_id": group[0].cell.id,
        "case_id": group[0].case.id,
        "repeat": group[0].repeat,
        "mapping": mapping,
    }
    if spec is None or not spec.command:
        payload["error"] = "missing_judge_command"
        payload["scores"] = {}
        _write_compare_result(dest, mapping, payload)
        return payload
    timeout_s = int(spec.timeout_s or 180)
    argv = resolve_argv(list(spec.command), root, {"experiment_root": str(root)})
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTLAB_")}
    env.pop("AGENTLAB_VARIANT", None)
    proc: subprocess.Popen | None = None
    stdout = stderr = b""
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(dest),
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
        payload["error"] = "judge_unavailable"
        payload["error_detail"] = "judge timed out"
    except Exception as exc:
        if proc is not None and proc.pid:
            kill_process_group(proc.pid)
        payload["error"] = "judge_unavailable"
        payload["error_detail"] = str(exc)
    _write_judge_logs(dest, stdout, stderr)
    if "error" not in payload:
        try:
            parsed = extract_json_payload((stdout or b"").decode(errors="replace"))
            if isinstance(parsed, dict):
                payload.update({k: parsed[k] for k in parsed if k != "mapping"})
            else:
                payload["error"] = "judge_bad_stdout"
        except Exception:
            payload["error"] = "judge_bad_stdout"
    _write_compare_result(dest, mapping, payload)
    return payload


def _write_compare_result(dest: Path, mapping: dict[str, str], payload: dict[str, Any]) -> None:
    (dest / "mapping.json").write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _compare_dir(root: Path, run_id: str | None, cell_id: str, case_id: str, repeat: int) -> Path:
    name = f"{case_id}__{cell_id}__r{repeat}"
    if run_id:
        return runs_dir(root) / run_id / "compare" / name
    return root / "trials" / ".compare" / name


def _blind_mapping(variant_ids: list[str], salt: str) -> dict[str, str]:
    unique = sorted(set(variant_ids))
    unique.sort(key=lambda vid: hashlib.sha256(f"{salt}:{vid}".encode()).hexdigest())
    return {LETTERS[i]: vid for i, vid in enumerate(unique)}


def _pack_label(dest: Path, label: str, trial: Trial) -> None:
    (dest / "patches").mkdir(exist_ok=True)
    diff = trial.outputs_dir() / "workspace.diff"
    patch_dest = dest / "patches" / f"{label}.diff"
    if diff.is_file():
        shutil.copy2(diff, patch_dest)
    else:
        patch_dest.write_text("", encoding="utf-8")
    after_src = trial.outputs_dir() / "after"
    after_dest = dest / "after" / label
    if after_src.is_dir():
        shutil.copytree(after_src, after_dest, dirs_exist_ok=True)
    else:
        after_dest.mkdir(parents=True, exist_ok=True)


def _compare_excerpt(root: Path, concerns: list[Concern]) -> str:
    parts = []
    for concern in concerns:
        text = criteria_for_judge(root, concern.id)
        if text.strip() and text.strip() not in parts:
            parts.append(text.strip())
    return "\n\n".join(parts)


def _compare_stdin(
    sample: Trial,
    concerns: list[Concern],
    mapping: dict[str, str],
    excerpt: str,
    prompt: str,
) -> str:
    labels = ", ".join(mapping)
    concern_ids = ", ".join(c.id for c in concerns)
    scores_shape = ",".join(f'"{c.id}":<number>' for c in concerns)
    parts = [
        "你是测评裁判，不是被测程序。",
        "当前目录是同一道题的几份匿名答卷，不是完整仓库。",
        f"答卷标记为 {labels}。主材料是 patches/<标记>.diff 和 after/<标记>/ 里的改后文件。",
        "先读补丁和改后文件。只有对某一处有疑问时，再点名去看 after 里的对应路径。不要一上来全盘搜索。",
        "不要猜测标记对应哪一版。不要修改文件。不要输出分析散文。",
        "stdout 给一篇 JSON 对象（前后可以有日志；不要用 markdown 围栏）：",
        "{"
        f'"ranking":["{next(iter(mapping))}"],'
        '"identical":[],'
        '"usable":{},'
        f'"scores":{{"{next(iter(mapping))}":{{{scores_shape}}}}},'
        '"evidence":{}'
        "}",
        f"scores 里为这些关注点打分：{concern_ids}。",
        "",
        "## 这次对比",
        f"cell_id: {sample.cell.id}",
        f"case_id: {sample.case.id}",
        f"repeat: {sample.repeat}",
        f"labels: {labels}",
        "",
    ]
    if prompt.strip():
        parts += ["## 任务说明", prompt.strip(), ""]
    parts += ["## 标准", excerpt.strip(), ""]
    parts += [
        "## 材料",
        "patches/ 下是各份补丁，after/ 下是各份改后文件。当前目录不是完整代码仓。",
        "",
    ]
    return "\n".join(parts)


def _apply_compare_scores(group: list[Trial], concerns: list[Concern], result: dict[str, Any]) -> None:
    mapping: dict[str, str] = result.get("mapping") or {}
    raw_scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
    error = result.get("error")
    by_variant = {t.variant.id: t for t in group}
    for label, vid in mapping.items():
        trial = by_variant.get(vid)
        if trial is None:
            continue
        existing = {s.concern_id: s for s in trial.scores}
        blob = raw_scores.get(label) if isinstance(raw_scores, dict) else None
        for concern in concerns:
            if error or not isinstance(blob, dict):
                existing[concern.id] = Score(
                    concern_id=concern.id,
                    unknown=True,
                    pass_=False,
                    evidence={"error_code": error or "judge_bad_stdout", "compare": True},
                )
                continue
            existing[concern.id] = _score_from_label(concern, blob, result)
        trial.scores = list(existing.values())


def _score_from_label(concern: Concern, blob: dict[str, Any], result: dict[str, Any]) -> Score:
    item = blob.get(concern.id)
    if isinstance(item, dict):
        payload = {"concern_id": concern.id, **item}
        try:
            score = _score_from_judge(payload, concern.id)
            score.evidence = {**(score.evidence or {}), "compare": True}
            return score
        except Exception:
            return Score(
                concern_id=concern.id,
                unknown=True,
                pass_=False,
                evidence={"error_code": "judge_bad_stdout", "compare": True},
            )
    if isinstance(item, (int, float, bool)):
        return Score(
            concern_id=concern.id,
            value=item,
            unknown=False,
            evidence={"compare": True, "ranking": result.get("ranking")},
        )
    return Score(
        concern_id=concern.id,
        unknown=True,
        pass_=False,
        evidence={"error_code": "judge_bad_stdout", "compare": True},
    )


def _write_trial_scores(trial: Trial) -> None:
    payload = [s.to_json() for s in trial.scores]
    trial.trial_dir().mkdir(parents=True, exist_ok=True)
    (trial.trial_dir() / "scores.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_compare_results(root: Path, run_id: str | None) -> list[dict[str, Any]]:
    ident = run_id
    bases = []
    if ident:
        bases.append(runs_dir(root) / ident / "compare")
    bases.append(root / "trials" / ".compare")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/result.json")):
            key = path.parent.name
            if key in seen:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                data["path"] = str(path)
                out.append(data)
                seen.add(key)
    return out
