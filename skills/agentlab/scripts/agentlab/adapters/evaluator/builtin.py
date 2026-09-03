from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

from agentlab.adapters.evaluator.regexes import COUNTERARG_NEEDLES, RE_ACTION, RE_DIRECTION, SECTION_PATTERNS
from agentlab.models import Score, Trial
from agentlab.schema import Concern, Experiment
from agentlab.templates import expand_templates
from agentlab.workspace import collect_changes


def builtin_evaluate(trial: Trial, concern: Concern, exp: Experiment, ctx: dict[str, str]) -> Score:
    mtype = concern.measure.type
    try:
        if mtype == "gold_tree":
            value, evidence = _gold_tree(trial, concern, ctx)
        elif mtype == "must_list":
            value, evidence = _must_list(trial, concern, ctx)
        elif mtype == "workspace_diff":
            value, evidence = _workspace_diff(trial, concern, ctx)
        elif mtype == "label_extract":
            value, evidence = _label_extract(trial, concern, exp, ctx)
        elif mtype == "section_present":
            value, evidence = _section_present(trial, concern, ctx)
        elif mtype == "counterarg_inline":
            value, evidence = _counterarg(trial, concern, ctx)
        elif mtype == "no_upgrade":
            value, evidence = _no_upgrade(trial, concern, exp, ctx)
        elif mtype == "path_under":
            value, evidence = _path_under(trial, concern, ctx)
        elif mtype == "cost":
            value, evidence = _cost(trial, concern)
        elif mtype == "static_size":
            value, evidence = _static_size(trial, concern, ctx)
        else:
            return Score(concern_id=concern.id, unknown=True, pass_=False, evidence={"error": f"unknown type {mtype}"})
        return Score(concern_id=concern.id, value=value, unknown=False, evidence=evidence, soft=concern.soft)
    except Exception as exc:  # evaluator must fail closed
        return Score(
            concern_id=concern.id,
            unknown=True,
            pass_=False,
            evidence={"error": str(exc), "error_code": "eval_failed"},
        )


def _project(trial: Trial) -> Path:
    assert trial.sandbox is not None
    return trial.sandbox.project_root


def _gold_tree(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    gold = expand_templates(concern.measure.gold_dir or "", ctx)
    gold_dir = Path(gold) if Path(gold).is_absolute() else trial.experiment_root / gold
    root = _project(trial)
    ignore = set(concern.measure.ignore or [])
    diffs: list[str] = []
    gold_files = [p for p in gold_dir.rglob("*") if p.is_file() and not _ignored(p.relative_to(gold_dir), ignore)]
    for gf in gold_files:
        rel = gf.relative_to(gold_dir)
        dest = root / rel
        if not dest.is_file() or dest.read_bytes() != gf.read_bytes():
            diffs.append(rel.as_posix())
    extra = [p for p in root.rglob("*") if p.is_file() and not _ignored(p.relative_to(root), ignore)]
    gold_rels = {p.relative_to(gold_dir).as_posix() for p in gold_files}
    include = concern.measure.include
    exclude = concern.measure.exclude
    for ef in extra:
        rel = ef.relative_to(root).as_posix()
        if not _in_scope(rel, include, exclude):
            continue
        if rel not in gold_rels:
            diffs.append(f"+{rel}")
    return (len(diffs) == 0, {"diffs": diffs[:50]})


def _ignored(rel: Path, ignore: set[str]) -> bool:
    text = rel.as_posix()
    if ".git" in Path(text).parts:
        return True
    for pat in ignore:
        if fnmatch.fnmatch(text, pat) or fnmatch.fnmatch(rel.name, pat):
            return True
    return False


def _in_scope(rel: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    if exclude and any(fnmatch.fnmatch(rel, pat) or rel.startswith(pat.rstrip("*")) for pat in exclude):
        return False
    if include:
        return any(fnmatch.fnmatch(rel, pat) or rel.startswith(pat.rstrip("*")) for pat in include)
    return True


def _must_list(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    root = _project(trial)
    keep = _read_list(trial, concern.measure.keep, ctx)
    gone = _read_list(trial, concern.measure.gone, ctx)
    include = concern.measure.include
    exclude = concern.measure.exclude
    missing, present = [], []
    for line in keep:
        if not _line_present(root, line, include, exclude):
            missing.append(line)
    for line in gone:
        if _line_present(root, line, include, exclude):
            present.append(line)
    return (not missing and not present, {"missing_keep": missing, "still_present": present})


def _read_list(trial: Trial, spec: str | None, ctx: dict[str, str]) -> list[str]:
    if not spec:
        return []
    path = Path(expand_templates(spec, ctx))
    if not path.is_absolute():
        path = trial.experiment_root / path
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _line_present(
    root: Path,
    line: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> bool:
    if "/" in line or Path(line).suffix:
        rel = Path(line).as_posix()
        if not _in_scope(rel, include, exclude):
            return False
        return (root / line).is_file()
    for file in root.rglob("*"):
        if not file.is_file() or ".git" in file.parts:
            continue
        rel = file.relative_to(root).as_posix()
        if not _in_scope(rel, include, exclude):
            continue
        try:
            if line in file.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def _workspace_diff(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    meta_path = trial.trial_dir() / "meta.json"
    snap: dict[str, str] | list[str] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        snap = meta.get("workspace_snap") or {}
    root = _project(trial)
    raw = collect_changes(root, snap)
    include = concern.measure.include
    exclude = concern.measure.exclude
    changes = [item for item in raw if _in_scope(item["path"], include, exclude)]
    allow = [expand_templates(x, ctx) for x in (concern.measure.allow_write or [])]
    forbid = [expand_templates(x, ctx) for x in (concern.measure.forbid_write or [])]
    bad = []
    for item in changes:
        path = item["path"]
        if forbid and any(fnmatch.fnmatch(path, f) or path.startswith(f.rstrip("*")) for f in forbid):
            bad.append(item)
            continue
        if allow and not any(fnmatch.fnmatch(path, a) or path.startswith(a.rstrip("*")) for a in allow):
            bad.append(item)
    return (not bad, {"changed": changes, "bad": bad})


def resolve_report_text(trial: Trial, concern: Concern, ctx: dict[str, str]) -> str:
    source = expand_templates(concern.measure.source or "", ctx)
    report_from = concern.measure.report_from or {}
    if report_from and Path(source).suffix == ".json":
        path = Path(source)
        if not path.is_file():
            path = trial.outputs_dir() / path.name
        if not path.is_file():
            raise FileNotFoundError(f"{path.name} missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        jp = report_from.get("json_path", "")
        key = jp.split(".")[-1] if isinstance(jp, str) else ""
        base = data.get(key.lstrip("$.")) if key else None
        suffix = report_from.get("suffix")
        if not base or not suffix:
            raise FileNotFoundError("report_from missing json_path or suffix")
        report = Path(base) / suffix
        return report.read_text(encoding="utf-8")
    if source in {"${report_path}", "report_path"} or concern.measure.source == "${report_path}":
        raise FileNotFoundError("report_path unbound")
    path = Path(source)
    if not path.is_absolute():
        path = trial.experiment_root / path
    return path.read_text(encoding="utf-8")


def _extract_labels(text: str, pattern: dict[str, str] | None) -> dict[str, str]:
    pats = pattern or {}
    direction_re = pats.get("direction") or RE_DIRECTION
    action_re = pats.get("action") or RE_ACTION
    # search only before 改变判断的条件
    cut = re.search(r"(?m)^#{0,3}\s*改变判断的条件\s*$", text)
    body = text[: cut.start()] if cut else text
    d = re.search(direction_re, body)
    a = re.search(action_re, body)
    out = {}
    if d:
        out["direction"] = d.group(1)
    if a:
        out["action"] = a.group(1)
    return out


def _expected_labels(trial: Trial, exp: Experiment) -> dict[str, Any]:
    case_dir = trial.experiment_root / (trial.case.path or f"cases/{trial.case.id}")
    yml = case_dir / "expected_labels.yaml"
    if yml.is_file():
        import yaml

        data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    return dict(trial.case.expected_labels or {})


def _label_extract(trial: Trial, concern: Concern, exp: Experiment, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    text = resolve_report_text(trial, concern, ctx)
    pattern = concern.measure.pattern if isinstance(concern.measure.pattern, dict) else None
    extracted = _extract_labels(text, pattern)
    expected = _expected_labels(trial, exp)
    labels = concern.measure.labels or list(expected)
    ok = True
    for key in labels:
        if expected.get(key) != extracted.get(key):
            ok = False
    if not extracted:
        raise ValueError("labels not extracted")
    return ok, {"extracted": extracted, "expected": expected}


def _section_present(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    text = resolve_report_text(trial, concern, ctx)
    missing = []
    for item in concern.measure.must_include or []:
        pat = SECTION_PATTERNS.get(item) or rf"(?m)^#{{0,3}}\s*{re.escape(item)}\s*$"
        if not re.search(pat, text):
            missing.append(item)
    return (not missing, {"missing": missing})


def _counterarg(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    text = resolve_report_text(trial, concern, ctx)
    basis = re.search(r"(?m)^#{0,3}\s*依据\s*$", text)
    change = re.search(r"(?m)^#{0,3}\s*改变判断的条件\s*$", text)
    start = basis.end() if basis else 0
    end = change.start() if change else len(text)
    body = text[start:end]
    needles = concern.measure.needles or COUNTERARG_NEEDLES
    hit = next((n for n in needles if n in body), None)
    return (hit is not None, {"hit": hit})


def _no_upgrade(trial: Trial, concern: Concern, exp: Experiment, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    text = resolve_report_text(trial, concern, ctx)
    extracted = _extract_labels(text, None)
    expected = _expected_labels(trial, exp)
    frm = concern.measure.from_
    to = concern.measure.to
    # expected.direction==无法判断 and extracted action==介入 → false
    if expected.get("direction") == frm and extracted.get("action") == to:
        return False, {"extracted": extracted, "expected": expected}
    return True, {"extracted": extracted, "expected": expected}


def _path_under(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    file_spec = expand_templates(concern.measure.file or "", ctx)
    path = Path(file_spec)
    if file_spec and not path.is_file():
        path = trial.outputs_dir() / Path(file_spec).name
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    jp = (concern.measure.json_path or "").lstrip("$").lstrip(".")
    cur: Any = data
    for part in jp.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None
            break
    target = Path(str(cur)).resolve() if cur else None
    prefix_env = concern.measure.prefix_env
    prefix = os.environ.get(prefix_env) if prefix_env else str(trial.outputs_dir().resolve())
    if target is None or not prefix:
        return False, {"target": str(cur), "prefix": prefix}
    ok = str(target).startswith(str(Path(prefix).resolve()))
    suffix = concern.measure.must_suffix
    if suffix and suffix not in str(target):
        ok = False
    return ok, {"target": str(target), "prefix": prefix}


def _cost(trial: Trial, concern: Concern) -> tuple[float, dict[str, Any]]:
    qty = concern.measure.quantity or "wall_clock_s"
    if trial.result is None:
        raise ValueError("no runner result")
    if qty == "wall_clock_s":
        return float(trial.result.wall_clock_s), {"source": "meta"}
    if qty == "tokens":
        usage = trial.result.usage
        if usage.tokens_unknown or (usage.tokens_in is None and usage.tokens_out is None):
            raise ValueError("tokens unknown")
        return float((usage.tokens_in or 0) + (usage.tokens_out or 0)), {"source": "usage"}
    if qty == "usd":
        if trial.result.usage.usd_unknown or trial.result.usage.usd is None:
            raise ValueError("usd unknown")
        return float(trial.result.usage.usd), {"source": "usage"}
    raise ValueError(f"unknown quantity {qty}")


def _static_size(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[float, dict[str, Any]]:
    rel = expand_templates(concern.measure.path or "SKILL.md", ctx)
    variant_path = trial.experiment_root / trial.variant.path / rel
    if not variant_path.is_file():
        raise FileNotFoundError(rel)
    data = variant_path.read_bytes()
    qty = concern.measure.quantity or "bytes"
    if qty == "bytes":
        return float(len(data)), {"path": str(variant_path)}
    return float(len(data) / 4), {"path": str(variant_path), "est": "chars/4"}
