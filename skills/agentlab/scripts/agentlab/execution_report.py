"""Pair archived executions for inspection; do not infer causes from scores."""
from __future__ import annotations

import difflib
import html
import math
from pathlib import Path

from agentlab.audit import Audit, CSS, JS, _json, relative_url, score_anchor
from agentlab.execution_audit import trial_anchor
from agentlab.provenance import atomic_json
from agentlab.storage import execution_path
from agentlab.gate import compare_op


class ExecutionAudit(Audit):
    def __init__(self, root: Path, run_id: str):
        super().__init__(root, run_id)
        self.path = self.run / 'execution.html'
        self.records = []
        for trial in sorted((self.run / 'trials').glob('*')):
            if self.manifest.get('planned') is not None and trial.name not in self.manifest['planned']:
                continue
            meta = self.read_json(trial / 'meta.json', {})
            if meta:
                self.records.append((trial, meta))

    def original(self, trial: Path, meta: dict) -> Path:
        ident = meta.get('execution_id')
        return execution_path(self.root, ident) if ident else trial / 'unavailable-execution'

    def baseline(self, meta: dict):
        return next(((path, other) for path, other in self.records if other.get('role') == 'baseline'
                     and all(other.get(k) == meta.get(k) for k in ('case_id', 'cell_id', 'repeat'))), None)

    def scores(self, trial: Path) -> dict:
        return {s['concern_id']: s for s in self.read_json(trial / 'scores.json', [])}

    def diagnose(self, trial: Path, meta: dict) -> dict:
        original = self.original(trial, meta)
        inputs = self.read_json(original / 'inputs/manifest.json', {})
        trace = self.read_json(original / 'trace/manifest.json', {})
        baseline = self.baseline(meta) if meta.get('role') != 'baseline' else None
        comparable = None
        if baseline:
            other = self.read_json(self.original(*baseline) / 'inputs/manifest.json', {})
            if inputs.get('comparison_context') and other.get('comparison_context') and not inputs.get('missing') and not other.get('missing'):
                comparable = inputs['comparison_context'] == other['comparison_context']
        findings, gaps = [], []
        execution_failed = bool(meta.get('execution_error') or meta.get('exit_code', 0) != 0 or meta.get('skipped') or 'exit_code' not in meta)
        if execution_failed:
            findings.append({'kind': 'execution_problem', 'detail': {k: meta.get(k) for k in ('error_code', 'exit_code', 'killed_reason', 'skipped')}})
        elif meta.get('error_code'):
            findings.append({'kind': 'trial_check_problem', 'detail': meta['error_code'],
                             'note': '被测命令已退出成功；需区分评分、隔离检查和后处理问题'})
        if not inputs or inputs.get('missing'):
            gaps.append('运行前输入快照缺失或不完整')
        if not trace.get('entries') or not any(not e.get('missing') and e.get('size', 0) > 0 for e in trace.get('entries', [])):
            gaps.append('没有可用的执行轨迹，无法还原搜索和工具调用过程')
        elif any(e.get('missing') for e in trace['entries']):
            gaps.append('部分声明的轨迹文件缺失')
        if meta.get('role') != 'baseline' and not baseline:
            gaps.append('本轮没有同 case、命令组和重复编号的对照；未自动借用其他运行')
        elif baseline and comparable is not True:
            gaps.append('两组执行条件不同' if comparable is False else '缺少两组输入快照，执行条件无法完整核对')
        scores = self.scores(trial)
        baseline_scores = self.scores(baseline[0]) if baseline else {}
        for cid, score in scores.items():
            if score.get('unknown'):
                findings.append({'kind': 'unknown_score', 'concern': cid, 'evidence': score.get('evidence')})
            concern = self.concern(self.run, cid)
            rule = concern.get('pass') or {}
            if not score.get('unknown') and rule.get('op') and rule.get('vs', 'value') == 'value':
                if not compare_op(score.get('value'), rule['op'], rule.get('value'), rule.get('margin', 0)):
                    findings.append({'kind': 'single_value_rule_miss', 'concern': cid,
                                     'value': score.get('value'), 'rule': rule,
                                     'note': '此原始值未满足单值条件；最终判定仍需按聚合方式和样本数阅读 promotion.json'})
            if comparable is not True or meta.get('error_code') or (baseline and baseline[1].get('error_code')):
                continue
            before = baseline_scores.get(cid, {})
            left, right = score.get('value'), before.get('value')
            if before.get('unknown') or score.get('unknown') or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (left, right)):
                continue
            higher = (concern.get('measure') or {}).get('higher_is_better')
            if higher is None:
                op = (concern.get('pass') or {}).get('op')
                higher = True if op in ('>', '>=') else False if op in ('<', '<=') else None
            if higher is not None and ((left < right) if higher else (left > right)):
                findings.append({'kind': 'weaker_measurement', 'concern': cid, 'baseline': right, 'candidate': left,
                                 'higher_is_better': higher, 'note': '单次配对数值按配置方向较弱，不代表统计退步或已证实原因'})
        return {'trial_id': trial.name, 'case_id': meta.get('case_id'), 'variant_id': meta.get('variant_id'),
                'baseline_trial': baseline[0].name if baseline else None,
                'execution_id': meta.get('execution_id'), 'comparable_declared_context': comparable,
                'observations': findings, 'evidence_gaps': gaps, 'causes': [],
                'causal_status': '尚未归因；需结合原始材料验证，不能从低分直接推导 Skill 缺陷'}

    def input_files(self, original: Path) -> str:
        manifest = self.read_json(original / 'inputs/manifest.json', {})
        links = [self.link(original / 'inputs' / rel, rel) for rel in list(manifest.get('files', {}))[:300]]
        return self.file('运行前输入清单（范围与缺失项）', original / 'inputs/manifest.json') + '<details><summary>运行前 Skill、任务与声明输入</summary>' + '<br>'.join(links) + '<p>最多列出 300 项，其余见完整清单。未声明的外部输入不在快照保证范围。</p></details>'

    def trace_files(self, original: Path) -> str:
        manifest = self.read_json(original / 'trace/manifest.json', {})
        body = self.file('执行轨迹清单', original / 'trace/manifest.json')
        for item in manifest.get('entries', [])[:30]:
            if item.get('path') and not item.get('missing'):
                body += self.file('执行轨迹 · ' + item['path'], original / 'trace/files' / item['path'])
        return '<details><summary>搜索、工具调用或子任务记录（由执行器导出）</summary><p>不默认把 stdout 当成完整会话；最多预览 30 份轨迹，完整列表见清单。导出记录的完整性未被独立核实。</p>' + body + '</details>'

    def execution(self, trial: Path, meta: dict) -> str:
        original = self.original(trial, meta)
        out = original / 'outputs' if (original / 'outputs').is_dir() else trial / 'outputs'
        body = self.input_files(original)
        for label, path in [('实际命令与运行参数', out / 'runner/execution.json'), ('被测程序收到的任务', original / 'inputs/prompt.md'),
                            ('执行 stdout', out / 'stdout.log'), ('执行 stderr', out / 'stderr.log'), ('文件改动', out / 'workspace.diff')]:
            body += self.file(label, path)
        body += '<p>' + self.link(out / 'diff.html', '阅读文件改动') + ' · ' + self.link(out, '全部执行产物') + '</p>'
        body += self.trace_files(original)
        return body

    def differences(self, trial: Path, meta: dict, base: Path, base_meta: dict) -> str:
        left, right = self.original(base, base_meta), self.original(trial, meta)
        before = self.read_json(left / 'inputs/manifest.json', {}).get('files', {})
        after = self.read_json(right / 'inputs/manifest.json', {}).get('files', {})
        changed = [rel for rel in sorted(before.keys() | after.keys())
                   if before.get(rel, {}).get('blob') != after.get(rel, {}).get('blob')]
        pairs = [(rel, left / 'inputs' / rel, right / 'inputs' / rel) for rel in changed[:30]]
        pairs += [(name, left / 'outputs' / name, right / 'outputs' / name) for name in ('stdout.log', 'stderr.log', 'workspace.diff')]
        body = '<p>输入内容差异及最终输出差异；最多展开 30 个输入文件。文本按审计预览上限截取，不能据此认定未显示部分相同。</p>'
        for label, old_path, new_path in pairs:
            old, new = self.read(old_path), self.read(new_path)
            diff = '\n'.join(difflib.unified_diff((old if old is not None else '未归档').splitlines(),
                             (new if new is not None else '未归档').splitlines(), fromfile=f'对照组/{label}', tofile=f'当前组/{label}', lineterm=''))
            body += self.block(label, diff or '可用预览内容相同；缺失材料无法比较。', self.link(old_path, '对照原文件') + ' · ' + self.link(new_path, '当前原文件'))
        return '<details><summary>与对照组比较输入和产物</summary>' + body + '</details>'

    def section(self, trial: Path, meta: dict, diagnosis: dict) -> str:
        baseline = self.baseline(meta) if meta.get('role') != 'baseline' else None
        heading = f"{meta.get('variant_id')} / {meta.get('cell_id')} / {meta.get('case_id')} / r{meta.get('repeat')}"
        status = 'fail' if diagnosis['observations'] else 'unrated'
        body = f'<article id="{trial_anchor(trial.name)}" data-outcome="{status}"><header><h2>{html.escape(heading)}</h2><p>{"有待排查的观测" if diagnosis["observations"] else "未发现规则可识别的异常"}</p></header>'
        body += '<p>执行来源：' + html.escape(str(meta.get('execution_id') or '未知')) + '</p>'
        observations = []
        for item in diagnosis['observations']:
            if item['kind'] == 'execution_problem':
                message = '执行未正常完成：' + _json(item['detail'])
            elif item['kind'] == 'trial_check_problem':
                message = '被测命令退出成功，但后续检查有问题：' + str(item['detail'])
            elif item['kind'] == 'unknown_score':
                message = f"{item['concern']}：评分为 unknown，需要检查证据和评分过程。"
            elif item['kind'] == 'single_value_rule_miss':
                message = f"{item['concern']}：当前值 {item['value']} 未满足单值条件；最终判定还需看聚合和样本数。"
            else:
                message = f"{item['concern']}：对照组 {item['baseline']} → 当前组 {item['candidate']}，按配置方向较弱。这是单次观测，原因尚未确定。"
            observations.append('<li>' + html.escape(message) + '</li>')
        body += '<ul>' + ''.join(observations) + '</ul>'
        if diagnosis['evidence_gaps']:
            body += '<p>排查还缺什么：</p><ul>' + ''.join('<li>' + html.escape(gap) + '</li>' for gap in diagnosis['evidence_gaps']) + '</ul>'
        body += self.block('已记录的现象与证据缺口', _json(diagnosis))
        scores = self.scores(trial)
        base_scores = self.scores(baseline[0]) if baseline else {}
        rows = []
        for cid, score in scores.items():
            url = relative_url(self.run / 'audit.html', self.path) + '#' + score_anchor(trial.name, cid)
            rows.append(f'<tr><td>{html.escape(cid)}</td><td>{html.escape(_json(base_scores.get(cid, {}).get("value")))}</td><td>{html.escape(_json(score.get("value")))}</td><td>{html.escape(str(score.get("unknown")))}</td><td><a href="{html.escape(url)}">评分审计</a></td></tr>')
        body += '<div class="table-wrap"><table><thead><tr><th>关注点</th><th>对照组原始值</th><th>当前组原始值</th><th>当前 unknown</th><th>依据</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><p>数值表不是最终通过判定；缺少方向时不自动判断好坏。两组评分来源和裁判输入见评分审计。</p>'
        body += self.execution(trial, meta)
        if baseline:
            body += self.differences(trial, meta, *baseline)
            body += f'<p><a href="#{trial_anchor(baseline[0].name)}">查看对照组完整执行记录</a></p>'
        body += '</article>'
        return body

    def write(self) -> Path:
        diagnostics = [self.diagnose(path, meta) for path, meta in self.records]
        atomic_json(self.run / 'diagnostics.json', {'run_id': self.run.name, 'trials': diagnostics,
                    'scope': 'Archived observations and evidence gaps; no automatic causal conclusions.'})
        body = f'<p class="eyebrow">AGENTLAB / RUN {html.escape(self.run.name)}</p><h1>执行排查</h1><p>同一 case 下，比较优化版与对照组的输入、执行记录和产物。</p>'
        body += '<aside>页面只记录可核对的现象，不把分数下降直接归因于 Skill。配对仅限当次运行中相同 case、命令组及重复编号；输入条件一致也不保证外部服务和随机性一致。</aside>'
        body += '<p>' + self.link(self.run / 'report.md', '运行报告') + ' · ' + self.link(self.run / 'diagnostics.json', '排查索引 JSON') + '</p>'
        body += '<nav><label>版本、用例或命令组 <input id="search" type="search" placeholder="输入关键词"></label><label>观测 <select id="outcome"><option value="">全部</option><option value="fail">有待排查的观测</option></select></label><span id="count" aria-live="polite"></span></nav>'
        body += ''.join(self.section(path, meta, diagnosis) for (path, meta), diagnosis in zip(self.records, diagnostics))
        css = CSS + 'article{overflow-wrap:anywhere}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px;border-bottom:1px solid #dbe3e8}th{white-space:nowrap}'
        self.path.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AgentLab 执行排查</title><style>' + css + '</style><main>' + body + '</main><script>' + JS.replace("' 条评分'", "' 次执行'") + '</script></html>', encoding='utf-8')
        return self.path


def write_execution_audit(root: Path, run_id: str) -> Path:
    return ExecutionAudit(root, run_id).write()
