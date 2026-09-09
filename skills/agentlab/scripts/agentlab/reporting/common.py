"""Bounded archive previews, stable links, and shared audit page presentation."""
from __future__ import annotations
import hashlib
import html
import json
import os
from pathlib import Path
from urllib.parse import quote
from agentlab.records.provenance import digest

PREVIEW_BYTES = 128 * 1024


def score_anchor(trial_id: str, concern_id: str) -> str:
    return 'score-' + hashlib.sha256(f'{trial_id}:{concern_id}'.encode()).hexdigest()[:16]


def relative_url(target: Path, document: Path) -> str:
    return quote(os.path.relpath(target, document.parent), safe='/')


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def trial_anchor(trial_id: str) -> str:
    return 'trial-' + digest(trial_id).split(':')[1][:16]


class ArchivePage:
    def __init__(self, root: Path, run_id: str):
        self.root = root.resolve()
        self.run = self.root / 'runs' / run_id
        self.manifest = self.read_json(self.run / 'manifest.json', {})

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
