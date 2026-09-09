from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

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
from agentlab.evidence import copy_evidence
from agentlab.runner.evaluation import judge_command
from agentlab.errors import BudgetExceeded
from agentlab.provenance import digest, measurement_basis

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
        basis = digest({"executions": sorted(t.execution_id or t.id for t in group),
                        "measurements": {c.id: [measurement_basis(exp, t, c) for t in group] for c in concerns}})
        previous = root / "runs" / (group[0].reused_from or "missing") / "compare" / dest.name
        if all(t.reused and not t.force_score and t.compare_basis == basis for t in group) and (previous / "result.json").is_file():
            if previous != dest:
                shutil.copytree(previous, dest, dirs_exist_ok=True)
            result = json.loads((dest / "result.json").read_text())
        elif any(t.error_code or t.skipped for t in group):
            result = {"mapping": _blind_mapping([t.variant.id for t in group], dest.name),
                      "error": "execution_failed", "scores": {}}
            dest.mkdir(parents=True, exist_ok=True)
            _write_compare_result(dest, result["mapping"], result)
        else:
            result = spawn_compare(exp, root, group, concerns, dest)
        for trial in group:
            trial.compare_basis = basis
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
    criteria_src = root / exp.criteria.path
    if criteria_src.is_file():
        shutil.copy2(criteria_src, dest / "criteria.md")
    excerpt = _compare_excerpt(root, concerns, exp.criteria.path)
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
    stdout = stderr = b""
    try:
        proc = judge_command(spec, argv, dest, dest, stdin_text, timeout_s, group[0].budget_tracker)
        stdout, stderr = proc.stdout, proc.stderr
        if proc.returncode != 0:
            payload["error"] = "judge_unavailable"
            payload["error_detail"] = f"judge exit {proc.returncode}"
    except BudgetExceeded as exc:
        payload["error"] = exc.reason
    except Exception as exc:
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
    copy_evidence(trial, dest / "evidence" / label)
    prompt_path = dest / "evidence" / label / "prompt.md"
    if prompt_path.is_file():
        text = prompt_path.read_text().replace(trial.id, label).replace(trial.variant.id, label)
        prompt_path.write_text(text, encoding="utf-8")
    manifest_path = dest / "evidence" / label / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("execution_id", None)
        for item in manifest.get("entries", []):
            item.pop("source", None)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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


def _compare_excerpt(root: Path, concerns: list[Concern], criteria_path: str = "criteria.md") -> str:
    parts = []
    for concern in concerns:
        text = criteria_for_judge(root, concern.id, criteria_path)
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
        "当前目录是同一道题的几份匿名答卷。evidence/<标记>/ 含回答、声明的文件和证据清单。",
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
    if isinstance(item, (int, float, bool)) and __import__("math").isfinite(float(item)):
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
