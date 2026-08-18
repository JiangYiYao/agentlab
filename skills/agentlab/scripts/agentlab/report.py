from __future__ import annotations

import json
from pathlib import Path

from agentlab.gate import evaluate_promotion
from agentlab.scheduler import load_current_records
from agentlab.schema import Experiment
from agentlab.stats import concern_stats, paired_deltas


def render_report(exp: Experiment, root: Path) -> str:
    records, stale = load_current_records(exp, root)
    promo = evaluate_promotion(exp, records)
    promo.ignored_stale = stale
    lines = [
        f"# Report: {exp.id}",
        "",
        "## 晋级",
        "",
        f"- system_ok: {promo.system_ok}",
    ]
    if not promo.variants:
        lines.append("- 无 treatment 在当前契约宇宙中")
    for vid, vp in promo.variants.items():
        lines.append(f"- `{vid}`: promotable={vp.promotable} recommend_ship={vp.recommend_ship}")
        for cell, ok in vp.cell_pass.items():
            lines.append(f"  - cell `{cell}`: {'pass' if ok else 'fail'}")
        for fail in vp.failures:
            lines.append(f"  - fail: {fail}")
        for obj in vp.objectives:
            lines.append(f"  - objective `{obj['id']}`: {'ok' if obj['ok'] else 'not ok'}")
    lines.extend(["", "## 关注点 × 格子", ""])
    by: dict[tuple[str, str], list[str]] = {}
    for rec in records:
        for cid, score in rec.scores.items():
            key = (cid, rec.cell_id)
            by.setdefault(key, []).append(
                f"`{rec.variant_id}` r{rec.repeat}: value={score.value} pass={score.pass_} unknown={score.unknown}"
            )
    for (cid, cell), rows in sorted(by.items()):
        lines.append(f"### {cid} @ {cell}")
        lines.extend(f"- {r}" for r in rows)
        lines.append("")
    lines.extend(["", "## 统计", ""])
    for item in concern_stats(exp, records):
        warn = f" **{item['warning']}**" if item.get("warning") else ""
        lines.append(
            f"- {item['concern']} / {item['cell']} / {item['variant']}: n={item['n']} mean={item['mean']} min={item['min']} max={item['max']}{warn}"
        )
    deltas = paired_deltas(exp, records)
    if deltas:
        lines.extend(["", "### paired Δ vs baseline", ""])
        for item in deltas:
            lines.append(
                f"- {item['concern']} / {item['cell']} / {item['variant']}: Δmean={item['delta_mean']} (n={item['n']})"
            )
    if stale:
        lines.extend(["", "## 附录：已忽略的陈旧 trial", ""])
        lines.extend(f"- {s}" for s in stale)
    lines.append("")
    return "\n".join(lines)


def write_report(exp: Experiment, root: Path, dest: Path | None = None) -> Path:
    text = render_report(exp, root)
    path = dest or (root / "report.md")
    path.write_text(text, encoding="utf-8")
    return path
