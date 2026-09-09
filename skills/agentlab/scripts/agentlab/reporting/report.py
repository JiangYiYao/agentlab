from __future__ import annotations

from pathlib import Path
import json

from agentlab.reporting.changes import write_run_diff
from agentlab.evaluation.gate import evaluate_promotion, Promotion, VariantPromotion
from agentlab.records.runs import latest_run_id, planned_ids_for_run, runs_dir, load_manifest
from agentlab.records.reader import load_current_records, load_compare_results
from agentlab.schema import Experiment
from agentlab.evaluation.stats import concern_stats, paired_deltas
from agentlab.reporting.scoring import write_audit
from agentlab.reporting.common import relative_url, score_anchor, trial_anchor
from agentlab.reporting.execution import write_execution_audit


def render_report(
    exp: Experiment,
    root: Path,
    *,
    run_id: str | None = None,
    trial_ids: list[str] | None = None,
    report_path: Path | None = None,
) -> str:
    ident = run_id or latest_run_id(root)
    manifest = load_manifest(root, ident) if ident else None
    if manifest and manifest.get("experiment"):
        exp = Experiment.model_validate(manifest["experiment"])
    planned = trial_ids if trial_ids is not None else planned_ids_for_run(root, ident)
    kwargs = {"historical": True} if manifest else {}
    records, stale = load_current_records(exp, root, trial_ids=planned, run_id=ident, **kwargs)
    scope = {key: {manifest[key]} if manifest and manifest.get(key) else None for key in ("only_variant", "only_cell", "only_case")}
    promo = evaluate_promotion(exp, records, only_variants=scope["only_variant"], only_cells=scope["only_cell"], only_cases=scope["only_case"])
    saved_decision = runs_dir(root) / ident / "promotion.json" if ident else None
    legacy = bool(manifest and not manifest.get("experiment"))
    if saved_decision and saved_decision.is_file():
        data = json.loads(saved_decision.read_text())
        promo = Promotion(variants={key: VariantPromotion(**value) for key, value in data.get("variants", {}).items()},
                          system_ok=data.get("system_ok", False), empty_required=data.get("empty_required", False),
                          ignored_stale=data.get("ignored_stale", []))
    elif legacy:
        promo = Promotion(variants={}, system_ok=False)
    else:
        promo.ignored_stale = stale
    lines = [
        f"# Report: {exp.id}",
        "",
        "## 这次运行",
        "",
        f"- run_id: {ident or '(none)'}",
        f"- planned: {len(planned) if planned is not None else 'all on disk'}",
        f"- scored: {len(records)}",
    ]
    if legacy:
        lines.append("- 旧运行没有契约快照：展示原始分数及已保存结论，不用当前规则重算历史判定。")
    if manifest:
        for key in ("status", "elapsed_s", "execution_s", "evaluation_s", "compare_s", "usage"):
            if key in manifest:
                lines.append(f"- {key}: {manifest[key]}")
        for key in ("ran", "reused", "rescored", "skipped", "env_unusable"):
            lines.append(f"- {key}: {len(manifest.get(key) or [])}")
    if ident and (runs_dir(root) / ident / "diff.html").is_file():
        lines.append(f"- 代码改动阅读: `runs/{ident}/diff.html`（用浏览器打开）")
    audit_url = relative_url(runs_dir(root) / ident / "audit.html", report_path or root / "report.md") if ident else None
    if audit_url:
        lines.append(f"- [评分审计]({audit_url})：查看任务、裁判输入、原始回复和评分来源（用浏览器打开）。")
        execution_url = relative_url(runs_dir(root) / ident / "execution.html", report_path or root / "report.md")
        lines.append(f"- [执行排查]({execution_url})：比较同一 case 的输入、执行轨迹及产物。")
    lines.extend(
        [
            "",
            "## 晋级",
            "",
            f"- system_ok: {'unknown' if legacy and not (saved_decision and saved_decision.is_file()) else promo.system_ok}",
        ]
    )
    if not promo.variants:
        lines.append("- 无已保存的晋级结论" if legacy else "- 无 treatment 在当前这次运行里")
    for vid, vp in promo.variants.items():
        lines.append(f"- `{vid}`: promotable={vp.promotable} recommend_ship={vp.recommend_ship}")
        for cell, ok in vp.cell_pass.items():
            lines.append(f"  - cell `{cell}`: {'pass' if ok else 'fail'}")
        for fail in vp.failures:
            lines.append(f"  - fail: {fail}")
        for obj in vp.objectives:
            status = obj.get("status") or ("ok" if obj.get("ok") else "not_ok")
            lines.append(f"  - objective `{obj['id']}`: {status}")
            for cell in obj.get("cells") or []:
                loc = cell.get("cell") or "-"
                if cell.get("case"):
                    loc = f"{loc}/{cell['case']}"
                bits = [f"value={cell.get('value')}"]
                if cell.get("baseline") is not None:
                    bits.append(f"baseline={cell['baseline']}")
                if cell.get("delta") is not None:
                    bits.append(f"Δ={cell['delta']}")
                bits.append(f"n={cell.get('n')}")
                if cell.get("unknown_n"):
                    bits.append(f"unknown={cell['unknown_n']}")
                lines.append(f"    - {loc}: {', '.join(bits)}")
    compares = load_compare_results(root, ident)
    if compares:
        lines.extend(["", "## 并排对比", ""])
        for item in compares:
            case = item.get("case_id") or "-"
            cell = item.get("cell_id") or "-"
            mapping = item.get("mapping") or {}
            ranking = item.get("ranking") or []
            named = [mapping.get(lab, lab) for lab in ranking]
            lines.append(f"- `{case}` / `{cell}` / r{item.get('repeat') or 1}: ranking={named or '-'}")
            identical = item.get("identical") or []
            if identical:
                lines.append(f"  - identical: {identical}")
            usable = item.get("usable") or {}
            if usable:
                shown = {mapping.get(k, k): v for k, v in usable.items()}
                lines.append(f"  - usable: {shown}")
            if item.get("error"):
                lines.append(f"  - error: {item['error']}")
    lines.extend(["", "## 关注点", ""])
    by: dict[tuple[str, str, str], list[str]] = {}
    for rec in records:
        for cid, score in rec.scores.items():
            key = (cid, rec.cell_id, rec.case_id)
            by.setdefault(key, []).append(
                f"`{rec.variant_id}` / `{rec.cell_id}` / `{rec.case_id}` / r{rec.repeat}: "
                f"value={score.value} pass={score.pass_} unknown={score.unknown}"
                + (f" · [审计]({audit_url}#{score_anchor(rec.trial_id, cid)})" if audit_url else "")
                + (f" · [排查执行]({execution_url}#{trial_anchor(rec.trial_id)})" if audit_url else "")
            )
    for (cid, cell, case), rows in sorted(by.items()):
        lines.append(f"### {cid} @ {cell} / {case}")
        lines.extend(f"- {r}" for r in rows)
        lines.append("")
    lines.extend(["", "## 统计", ""])
    for item in concern_stats(exp, records):
        warn = f" **{item['warning']}**" if item.get("warning") else ""
        case = item.get("case") or "-"
        lines.append(
            f"- {item['concern']} / {item['cell']} / {case} / {item['variant']}: "
            f"n={item['n']} mean={item['mean']} min={item['min']} max={item['max']}{warn}"
        )
    deltas = paired_deltas(exp, records)
    if deltas:
        lines.extend(["", "### paired Δ vs baseline", ""])
        for item in deltas:
            case = item.get("case") or "-"
            lines.append(
                f"- {item['concern']} / {item['cell']} / {case} / {item['variant']}: "
                f"Δmean={item['delta_mean']} (n={item['n']})"
            )
    if stale:
        lines.extend(["", "## 附录：已忽略的陈旧 trial", ""])
        lines.extend(f"- {s}" for s in stale)
    lines.append("")
    return "\n".join(lines)


def write_report(
    exp: Experiment,
    root: Path,
    dest: Path | None = None,
    *,
    run_id: str | None = None,
    trial_ids: list[str] | None = None,
) -> Path:
    ident = run_id or latest_run_id(root)
    planned = trial_ids if trial_ids is not None else planned_ids_for_run(root, ident)
    if ident and planned:
        write_run_diff(root, ident, planned)
    path = dest or (root / "report.md")
    text = render_report(exp, root, run_id=ident, trial_ids=trial_ids, report_path=path)
    path.write_text(text, encoding="utf-8")
    if ident and dest is None:
        run_report = runs_dir(root) / ident / "report.md"
        run_report.parent.mkdir(parents=True, exist_ok=True)
        run_report.write_text(render_report(exp, root, run_id=ident, trial_ids=trial_ids, report_path=run_report), encoding="utf-8")
    if ident:
        write_execution_audit(root, ident)
        write_audit(root, ident)
    return path
