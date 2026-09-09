"""Read-only human audit view built exclusively from archived run artifacts."""
from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
from pathlib import Path
from urllib.parse import quote

PREVIEW_BYTES = 128 * 1024


def score_anchor(trial_id: str, concern_id: str) -> str:
    return 'score-' + hashlib.sha256(f'{trial_id}:{concern_id}'.encode()).hexdigest()[:16]


def relative_url(target: Path, document: Path) -> str:
    return quote(os.path.relpath(target, document.parent), safe='/')


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


class Audit:
    def __init__(self, root: Path, run_id: str):
        self.root = root.resolve()
        self.run = self.root / 'runs' / run_id
        self.path = self.run / 'audit.html'
        self.manifest = self.read_json(self.run / 'manifest.json', {})
        self.history = sorted((p for p in (self.root / 'runs').iterdir()
                               if p.is_dir() and p.name < run_id), reverse=True)

    def safe(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.root)

    def read(self, path: Path) -> str | None:
        if not self.safe(path) or not path.is_file():
            return None
        with path.open('rb') as stream:
            raw = stream.read(PREVIEW_BYTES + 1)
        value = raw[:PREVIEW_BYTES].decode('utf-8', errors='replace')
        if len(raw) > PREVIEW_BYTES:
            value += '\n[…预览已截断，请查看原文件…]'
        return value

    def read_json(self, path: Path, default):
        # Structural metadata needs to be complete; text previews remain bounded.
        if not self.safe(path) or not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def link(self, path: Path, label: str, anchor: str = '') -> str:
        if not self.safe(path) or not path.exists():
            return html.escape(label) + '（未归档或不可用）'
        url = relative_url(path, self.path) + ('#' + anchor if anchor else '')
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'

    def block(self, title: str, text: str | None, link: str = '') -> str:
        body = html.escape(text) if text is not None else '未归档，无法核实。'
        return f'<details><summary>{html.escape(title)}</summary>{link}<pre>{body}</pre></details>'

    def file(self, title: str, path: Path) -> str:
        return self.block(title, self.read(path), self.link(path, '查看原文件'))

    def concern(self, run: Path, cid: str) -> dict:
        manifest = self.read_json(run / 'manifest.json', {})
        return next((c for c in (manifest.get('experiment') or {}).get('concerns', []) if c.get('id') == cid), {})

    def view(self, run: Path, tid: str, cid: str, score: dict) -> Path | None:
        trial = run / 'trials' / tid
        meta = self.read_json(trial / 'meta.json', {})
        concern = self.concern(run, cid)
        kind = (concern.get('measure') or {}).get('type')
        if (score.get('evidence') or {}).get('compare'):
            return run / 'compare' / f"{meta.get('case_id')}__{meta.get('cell_id')}__r{meta.get('repeat')}"
        for folder, expected in [('judges', 'llm_rubric'), ('evaluators', 'script')]:
            view = trial / 'outputs' / folder / cid
            if kind == expected or (not kind and self.safe(view) and view.is_dir()):
                return view
        return None

    def snapshot(self, run: Path, tid: str, cid: str, score: dict) -> dict:
        out = run / 'trials' / tid / 'outputs'
        view = self.view(run, tid, cid, score)
        def first(*paths):
            return next((value for p in paths if (value := self.read(p)) is not None), None)
        return {
            '任务': first(view / 'prompt.md', out / 'evidence/prompt.md', out / 'prompt.md') if view else first(out / 'evidence/prompt.md', out / 'prompt.md'),
            '裁判输入': self.read(view / 'stdin.md') if view else None,
            '评审标准': first(view / 'criteria-excerpt.md', view / 'criteria.md') if view else self.read(run / 'criteria.md'),
            '本轮关注点配置': _json(self.concern(run, cid)) if self.concern(run, cid) else None,
            '调用配置': _json({k: v for k, v in self.read_json(view / 'execution.json', {}).items()
                               if k in {'command', 'prompt_mode', 'timeout_s', 'inherit_host_identity'}}) if view else None,
            '被测输出 stdout': self.read(out / 'stdout.log'),
            '文件改动': self.read(out / 'workspace.diff'),
            '被测证据清单': self.read(out / 'evidence/manifest.json'),
            '裁判或脚本回复': self.read(view / 'stdout.log') if view else None,
            '脚本 JSON 输出': self.read(view / 'output.json') if view else None,
            '解析分数': _json(score),
        }

    def previous(self, tid: str, cid: str):
        source = self.manifest.get('source_run')
        candidates = [self.root / 'runs' / source] if isinstance(source, str) and source != self.run.name else self.history
        for run in candidates:
            if not self.safe(run):
                continue
            for score in self.read_json(run / 'trials' / tid / 'scores.json', []):
                if score.get('concern_id') == cid:
                    return run, score
        return None

    def materials(self, view: Path, compare: bool, *, judge: bool = True) -> str:
        manifests = sorted((view / 'evidence').glob('*/manifest.json')) if compare else [view / 'evidence/manifest.json']
        parts = []
        for path in manifests:
            data = self.read_json(path, {})
            rows = []
            for item in data.get('entries', [])[:300]:
                rel = item.get('path', '')
                status = '缺失' if item.get('missing') else ('截断' if item.get('truncated') else '已提供')
                rows.append(f'<li>{html.escape(status)} · {self.link(path.parent / rel, str(rel))}</li>')
            extra = '<p>仅列出前 300 项；完整清单见原文件。</p>' if len(data.get('entries', [])) > 300 else ''
            parts.append(self.link(path, str(path.relative_to(view))) + '<ul>' + ''.join(rows) + '</ul>' + extra)
        if not parts:
            parts.append('<p>没有保存材料清单，无法核实完整性。</p>')
        for name in ('workspace', 'after', 'patches', 'changes.diff', 'changes.txt'):
            target = view / name
            if self.safe(target) and target.exists():
                parts.append('<p>' + self.link(target, name) + '</p>')
        title = '提供给裁判的材料' if judge else '被测执行证据与文件'
        note = '<p>“已提供”表示材料存在于评审目录；不证明模型实际读取或理解了它。</p>' if judge else ''
        return f'<details><summary>{title}</summary>' + note + ''.join(parts) + '</details>'

    def section(self, trial: Path, score: dict) -> str:
        tid, cid = trial.name, str(score.get('concern_id', ''))
        meta = self.read_json(trial / 'meta.json', {})
        event = (meta.get('evaluation_events') or {}).get(cid, {})
        status = {'evaluated': '本轮评分', 'reused': '复用旧分数', 'not_run': '未执行评分'}.get(event.get('status'), '评分来源未知（旧记录或未记录）')
        execution = '新执行' if tid in self.manifest.get('ran', []) else ('复用执行' if tid in self.manifest.get('reused', []) else '执行状态未知或未执行')
        summary = f"{meta.get('variant_id', tid)} / {meta.get('cell_id', '?')} / {meta.get('case_id', '?')} / r{meta.get('repeat', '?')}"
        value = 'unknown' if score.get('unknown') else str(score.get('value'))
        outcome = 'unknown' if score.get('unknown') else ('fail' if score.get('pass') is False else 'pass' if score.get('pass') is True else 'unrated')
        parts = [f'<article id="{score_anchor(tid, cid)}" data-outcome="{outcome}"><header><p class="eyebrow">{html.escape(summary)}</p><h2>{html.escape(cid)} <span class="badge {outcome}">{html.escape(value)}</span></h2><p>{execution} · {status} · 原始 pass={html.escape(_json(score.get("pass")))}</p></header>']
        source = event.get('source_run')
        if isinstance(source, str):
            source_trial = self.root / 'runs' / source / 'trials' / tid
            verified = '已记录评分来源' if event.get('source_verified') else '仅知复用入口，原始评分来源未核实'
            parts.append('<p>' + verified + '：' + self.link(source_trial / 'scores.json', source) + '</p>')
        if event.get('reason'):
            parts.append('<p>' + html.escape(str(event['reason'])) + '</p>')
        parts.append(self.block('解析分数与证据', _json(score), self.link(trial / 'scores.json', 'scores.json')))
        decision = self.read_json(self.run / 'promotion.json', {}).get('variants', {}).get(meta.get('variant_id'))
        if decision is not None:
            parts.append(self.block('本轮汇总判定（该版本，含跨试验统计）', _json(decision), self.link(self.run / 'promotion.json', '完整晋级判定')))
        view = self.view(self.run, tid, cid, score)
        snap = self.snapshot(self.run, tid, cid, score)
        parts.append(self.block('任务内容（归档版本）', snap['任务']))
        parts.append(self.block('本轮关注点与通过条件', snap['本轮关注点配置']))
        if view and (view.parent.name == 'judges' or view.parent.name == 'compare'):
            parts.append(self.file('完整裁判输入 · AgentLab 交给裁判命令的内容', view / 'stdin.md'))
            parts.append(self.file('本次裁判使用的标准', view / 'criteria-excerpt.md'))
            parts.append(self.file('完整标准文件', view / 'criteria.md'))
            if view.parent.name == 'compare':
                parts.append(self.file('匿名答卷与版本对应关系', view / 'mapping.json'))
                parts.append(self.file('并排评审解析结果', view / 'result.json'))
            parts.append(self.materials(view, view.parent.name == 'compare'))
        elif not view:
            parts.append('<p>内置测量或系统判定没有裁判提示词；请结合关注点配置与解析证据检查结果。</p>')
        if view:
            parts.append(self.file('实际调用记录（命令、耗时、退出状态）', view / 'execution.json'))
            parts.append(self.file('原始 stdout（裁判回复或脚本输出）', view / 'stdout.log'))
            parts.append(self.file('原始 stderr', view / 'stderr.log'))
            if view.parent.name == 'evaluators':
                parts.append(self.file('脚本解析结果（通过条件应用前）', view / 'result.json'))
                if (view / 'output.json').is_file():
                    parts.append(self.file('脚本 JSON 输出快照', view / 'output.json'))
        parts.append(self.file('被测程序 stdout', trial / 'outputs/stdout.log'))
        from agentlab.execution_audit import trial_anchor
        parts.append('<p>' + self.link(self.run / 'execution.html', '排查本次执行并与对照组比较', trial_anchor(tid)) + '</p>')
        parts.append(self.file('被测执行证据清单', trial / 'outputs/evidence/manifest.json'))
        parts.append(self.materials(trial / 'outputs', False, judge=False))
        parts.append(self.block('试验元数据与评分来源记录', _json(meta), self.link(trial / 'meta.json', 'meta.json')))
        previous = self.previous(tid, cid)
        if previous:
            prev, old_score = previous
            old = self.snapshot(prev, tid, cid, old_score)
            diff = []
            for name, current in snap.items():
                before = old.get(name)
                if before != current:
                    diff.extend(difflib.unified_diff((before if before is not None else '未归档，无法核实').splitlines(),
                                (current if current is not None else '未归档，无法核实').splitlines(),
                                fromfile=f'{prev.name}/{name}', tofile=f'{self.run.name}/{name}', lineterm=''))
            link = self.link(prev / 'audit.html', '打开上次审计', score_anchor(tid, cid)) + ' · ' + self.link(prev / 'trials' / tid / 'scores.json', '上次原始分数')
            parts.append(self.block(f'与 {prev.name} 对比（同一试验与关注点）', '\n'.join(diff) if diff else '可用归档字段未变化。缺失字段无法比较。', link))
            parts.append('<p class="muted">对比对象为指定的重评来源，或此前最近一次同名试验。文本超过预览上限时只比较预览；完整材料请打开原文件。</p>')
        else:
            parts.append('<p class="muted">没有找到可对比的前一次评分。</p>')
        parts.append('</article>')
        return ''.join(parts)

    def write(self) -> Path:
        sections = []
        wanted = self.manifest.get('planned')
        for trial in sorted((self.run / 'trials').glob('*')):
            if wanted is not None and trial.name not in wanted:
                continue
            for score in self.read_json(trial / 'scores.json', []):
                sections.append(self.section(trial, score))
        heading = f'<p class="eyebrow">AGENTLAB / RUN {html.escape(self.run.name)}</p><h1>评分审计</h1><p>从一个分数追溯到任务、标准、评审输入和原始结果。</p><p>{len(sections)} 条评分 · 新执行 {len(self.manifest.get("ran", []))} 次 · 复用执行 {len(self.manifest.get("reused", []))} 次</p>'
        notice = '<aside>这里展示 AgentLab 保存的调用输入和材料。外部 CLI 添加的系统指令、历史会话、实际使用的模型及文件读取行为，未被独立核实。旧记录缺失的材料不会用当前文件补齐。<br>原始 pass 是测量结果中的字段；最终是否满足要求，还取决于通过条件和跨试验统计，请结合汇总判定阅读。</aside>'
        nav = '<nav><label>查找版本、用例或关注点 <input id="search" type="search" placeholder="输入关键词"></label><label>原始分数状态 <select id="outcome"><option value="">全部</option><option value="fail">pass=false</option><option value="unknown">Unknown</option><option value="pass">pass=true</option><option value="unrated">未提供 pass</option></select></label><span id="count" aria-live="polite"></span></nav>'
        body = heading + notice + '<p>' + self.link(self.run / 'manifest.json', '运行配置快照') + ' · ' + self.link(self.run / 'report.md', '运行报告') + ' · ' + self.link(self.run / 'promotion.json', '汇总判定') + '</p>' + nav + ''.join(sections)
        if not sections:
            body += '<p>这次运行没有已归档的评分。</p>'
        self.path.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AgentLab 评分审计</title><style>' + CSS + '</style><main>' + body + '</main><script>' + JS + '</script></html>', encoding='utf-8')
        return self.path


CSS = '''
:root{font-family:system-ui,-apple-system,sans-serif;color:#182b39;background:#f4f6f8;line-height:1.6}
*{box-sizing:border-box}main{max-width:1100px;margin:auto;padding:36px 24px 80px}h1{font-size:36px;margin:8px 0}h2{font-size:22px;margin:4px 0}.eyebrow{font-size:13px;color:#536775;overflow-wrap:anywhere}a{color:#096789}aside{background:#e8f1f5;padding:16px;border-left:3px solid #50859a;border-radius:4px}nav{display:flex;gap:18px;align-items:end;flex-wrap:wrap;margin:24px 0}label{display:grid;gap:6px}input,select{padding:10px;border:1px solid #bccad3;border-radius:6px;font:inherit}input{width:min(380px,75vw)}article{background:white;border:1px solid #dbe3e8;border-radius:12px;margin:22px 0;padding:24px;scroll-margin-top:16px}article:target{outline:3px solid #428da9}article[hidden]{display:none}header p{margin:4px 0 12px}.badge{font-size:16px;padding:3px 10px;background:#edf1f4;border-radius:6px}.fail{background:#fce9e8;color:#983d34}.unknown{background:#fff1d9;color:#805a21}.pass{background:#e5f3eb;color:#246746}details{border-top:1px solid #e7ecef;padding:12px 0}summary{cursor:pointer;font-weight:600}details a{display:inline-block;margin-top:10px}pre{background:#f5f7f9;border-radius:6px;padding:16px;overflow:auto;max-height:560px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}.muted{color:#657581;font-size:13px}li{overflow-wrap:anywhere}@media(max-width:600px){main{padding:20px 12px}article{padding:16px}h1{font-size:28px}}@media print{nav{display:none}article{break-inside:avoid}}
'''
JS = '''
const search=document.getElementById('search'), outcome=document.getElementById('outcome');
const cards=[...document.querySelectorAll('article')];
function filter(){const q=search.value.toLowerCase();let n=0;for(const card of cards){card.hidden=!(card.querySelector('header').textContent.toLowerCase().includes(q)&&(!outcome.value||card.dataset.outcome===outcome.value));if(!card.hidden)n++;}document.getElementById('count').textContent=n+' 条评分';}
search.addEventListener('input',filter);outcome.addEventListener('change',filter);filter();
function revealTarget(){const card=document.getElementById(location.hash.slice(1));if(card&&card.matches('article')){if(card.hidden){search.value='';outcome.value='';filter();}card.scrollIntoView();}}
window.addEventListener('hashchange',revealTarget);revealTarget();
'''


def write_audit(root: Path, run_id: str) -> Path:
    return Audit(root, run_id).write()
