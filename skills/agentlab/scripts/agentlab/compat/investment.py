from __future__ import annotations

import re
from typing import Any
"""Legacy investment-report conventions; new experiments should declare patterns."""
RE_SECTION_CONCLUSION = r"(?m)^#{0,3}\s*结论\s*$"
RE_SECTION_BASIS = r"(?m)^#{0,3}\s*依据\s*$"
RE_SECTION_CHANGE = r"(?m)^#{0,3}\s*改变判断的条件\s*$"
RE_DIRECTION = r"(?:方向)\s*[：:为是]?\s*(偏多|中性|偏空|无法判断)"
RE_ACTION = r"(?:当前)?动作\s*[：:为是]?\s*(介入|等待|回避)"
COUNTERARG_NEEDLES = ["最强反证", "尚未改变结论", "尚未推翻", "为何尚未推翻", "为何尚未改变"]
SECTION_PATTERNS = {
    "结论": RE_SECTION_CONCLUSION,
    "依据": RE_SECTION_BASIS,
    "改变判断的条件": RE_SECTION_CHANGE,
}


def extract_labels(text: str, pattern: dict[str, str] | None) -> dict[str, str]:
    if pattern:
        result = {}
        for key, expression in pattern.items():
            match = re.search(expression, text)
            if match:
                result[key] = match.group(1) if match.lastindex else match.group(0)
        return result
    pats = {}
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




def counterarg(trial: Trial, concern: Concern, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    from agentlab.adapters.evaluator.builtin import resolve_report_text, _expected_labels, _extract_labels
    text = resolve_report_text(trial, concern, ctx)
    basis = re.search(r"(?m)^#{0,3}\s*依据\s*$", text)
    change = re.search(r"(?m)^#{0,3}\s*改变判断的条件\s*$", text)
    start = basis.end() if basis else 0
    end = change.start() if change else len(text)
    body = text[start:end]
    needles = concern.measure.needles or COUNTERARG_NEEDLES
    hit = next((n for n in needles if n in body), None)
    return (hit is not None, {"hit": hit})


def no_upgrade(trial: Trial, concern: Concern, exp: Experiment, ctx: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    from agentlab.adapters.evaluator.builtin import resolve_report_text, _expected_labels, _extract_labels
    text = resolve_report_text(trial, concern, ctx)
    extracted = _extract_labels(text, None)
    expected = _expected_labels(trial, exp)
    frm = concern.measure.from_
    to = concern.measure.to
    # expected.direction==无法判断 and extracted action==介入 → false
    if expected.get("direction") == frm and extracted.get("action") == to:
        return False, {"extracted": extracted, "expected": expected}
    return True, {"extracted": extracted, "expected": expected}


