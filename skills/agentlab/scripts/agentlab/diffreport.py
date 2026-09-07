from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab.models import Trial
from agentlab.runs import runs_dir
from agentlab.workspace import collect_changes, git_roots

STATUS_LABEL = {"A": "新增", "M": "修改", "D": "删除", "R": "重命名", "U": "未跟踪"}
MAX_FILE_BYTES = 512 * 1024
MAX_PATCH_LINES = 4000


@dataclass
class FilePatch:
    path: str
    status: str
    from_path: str | None = None
    patch: str = ""
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    truncated: bool = False
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "from": self.from_path,
            "additions": self.additions,
            "deletions": self.deletions,
            "binary": self.binary,
            "truncated": self.truncated,
            "note": self.note,
        }


@dataclass
class DiffBundle:
    files: list[FilePatch] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(item.additions for item in self.files)

    @property
    def deletions(self) -> int:
        return sum(item.deletions for item in self.files)


def collect_patches(project_root: Path, snap: dict[str, str] | list[str] | None = None) -> DiffBundle:
    changes = collect_changes(project_root, snap or {})
    roots = git_roots(project_root)
    return DiffBundle(files=[_patch_one(project_root, roots, item) for item in changes])


def write_trial_diff(trial: Trial) -> Path | None:
    if trial.sandbox is None or not trial.sandbox.project_root.is_dir():
        return None
    snap: dict[str, str] | list[str] = {}
    meta_path = trial.trial_dir() / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                snap = meta.get("workspace_snap") or {}
        except (OSError, json.JSONDecodeError):
            snap = {}
    bundle = collect_patches(trial.sandbox.project_root, snap)
    out = trial.outputs_dir()
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "trial_id": trial.id,
        "variant_id": trial.variant.id,
        "cell_id": trial.cell.id,
        "case_id": trial.case.id,
        "repeat": trial.repeat,
        "files": len(bundle.files),
        "additions": bundle.additions,
        "deletions": bundle.deletions,
        "paths": [item.to_json() for item in bundle.files],
    }
    (out / "diff.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "workspace.diff").write_text(_bundle_to_diff(bundle), encoding="utf-8")
    _write_after_files(out / "after", trial.sandbox.project_root, bundle)
    title = f"{trial.variant.id} / {trial.cell.id} / {trial.case.id} / r{trial.repeat}"
    html = render_trial_html(trial.id, title, bundle)
    dest = out / "diff.html"
    dest.write_text(html, encoding="utf-8")
    return dest


def _write_after_files(after_root: Path, project_root: Path, bundle: DiffBundle) -> None:
    if after_root.exists():
        shutil.rmtree(after_root)
    after_root.mkdir(parents=True, exist_ok=True)
    for item in bundle.files:
        src = project_root / item.path
        if item.status == "D" or not src.is_file():
            continue
        dest = after_root / item.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.stat().st_size > MAX_FILE_BYTES:
                continue
            shutil.copy2(src, dest)
        except OSError:
            continue


def _bundle_to_diff(bundle: DiffBundle) -> str:
    chunks: list[str] = []
    for item in bundle.files:
        if item.patch:
            chunks.append(item.patch.rstrip() + "\n")
            continue
        line = f"# {item.status} {item.path}"
        if item.from_path:
            line += f" <- {item.from_path}"
        if item.note:
            line += f" ({item.note})"
        chunks.append(line + "\n")
    return "\n".join(chunks)


def write_run_diff(root: Path, run_id: str, trial_ids: list[str] | None) -> Path | None:
    items: list[dict[str, Any]] = []
    for tid in trial_ids or []:
        payload = _load_trial_summary(root, run_id, tid)
        if payload is not None:
            items.append(payload)
    if not items:
        return None
    html = render_run_html(run_id, items)
    dest = runs_dir(root) / run_id / "diff.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    return dest


def _load_trial_summary(root: Path, run_id: str, trial_id: str) -> dict[str, Any] | None:
    for base in (runs_dir(root) / run_id / "trials" / trial_id, root / "trials" / trial_id):
        path = base / "outputs" / "diff.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data["href"] = f"trials/{trial_id}/outputs/diff.html"
            return data
    return None


def _patch_one(project_root: Path, roots: list[Path], item: dict[str, str]) -> FilePatch:
    rel = item["path"]
    status = item["status"]
    from_path = item.get("from")
    mapped = _repo_for(project_root, roots, rel)
    if mapped is not None:
        repo, inner = mapped
        from_inner = None
        if from_path:
            mapped_from = _repo_for(project_root, roots, from_path)
            if mapped_from and mapped_from[0] == repo:
                from_inner = mapped_from[1]
        patch, binary = _git_patch(repo, inner, status, from_inner)
        if patch or binary:
            patch, truncated = _truncate(patch)
            adds, dels = _count_lines(patch)
            return FilePatch(
                path=rel,
                status=status,
                from_path=from_path,
                patch=_normalize_headers(patch, rel, from_path),
                additions=adds,
                deletions=dels,
                binary=binary,
                truncated=truncated,
            )
    return _hash_patch(project_root, item)


def _repo_for(project_root: Path, roots: list[Path], rel: str) -> tuple[Path, str] | None:
    posix = Path(rel).as_posix()
    root_res = project_root.resolve()
    for repo in sorted(roots, key=lambda p: len(p.parts), reverse=True):
        try:
            prefix = repo.resolve().relative_to(root_res).as_posix()
        except ValueError:
            continue
        if prefix in {".", ""}:
            return repo, posix
        if posix == prefix or posix.startswith(prefix + "/"):
            return repo, posix[len(prefix) :].lstrip("/")
    return None


def _git_patch(repo: Path, inner: str, status: str, from_inner: str | None) -> tuple[str, bool]:
    paths = [inner]
    if from_inner and from_inner != inner:
        paths = [from_inner, inner]
    text, binary = _run_git(["git", "-C", str(repo), "diff", "--no-color", "-M", "HEAD", "--", *paths])
    if text.strip() or binary:
        return text, binary
    if status in {"U", "A"}:
        return _run_git(
            ["git", "-C", str(repo), "diff", "--no-color", "--no-index", "--", os.devnull, inner]
        )
    return text, binary


def _run_git(argv: list[str]) -> tuple[str, bool]:
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=30)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return "", False
    raw = proc.stdout or b""
    text = raw.decode("utf-8", errors="replace")
    binary = b"\x00" in raw[:8192] or "Binary files" in text or "GIT binary patch" in text
    return text, binary


def _hash_patch(project_root: Path, item: dict[str, str]) -> FilePatch:
    rel = item["path"]
    status = item["status"]
    path = project_root / rel
    if status in {"A", "U"}:
        return _file_as_added(rel, status, path)
    if status == "D":
        return FilePatch(path=rel, status=status, note="已删除（没有 git，无法展示改前内容）")
    if status == "R":
        new = _file_as_added(rel, status, path)
        new.from_path = item.get("from")
        new.note = f"由 {item.get('from')} 重命名而来（没有 git，只展示改后内容）"
        return new
    preview = _file_as_added(rel, status, path)
    preview.additions = 0
    preview.note = "已修改（没有 git，无法展示改前内容；下面是改后全文）"
    if preview.patch:
        preview.patch = "\n".join(
            line[1:] if line.startswith("+") else line for line in preview.patch.splitlines()
        )
    return preview


def _file_as_added(rel: str, status: str, path: Path) -> FilePatch:
    if not path.is_file():
        return FilePatch(path=rel, status=status, note="文件已不在")
    try:
        size = path.stat().st_size
    except OSError:
        return FilePatch(path=rel, status=status, note="无法读取")
    if size > MAX_FILE_BYTES:
        return FilePatch(path=rel, status=status, truncated=True, note=f"文件过大（{size} 字节），未展开")
    try:
        data = path.read_bytes()
    except OSError:
        return FilePatch(path=rel, status=status, note="无法读取")
    if b"\x00" in data[:8192]:
        return FilePatch(path=rel, status=status, binary=True, note="二进制文件")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    patch = f"--- /dev/null\n+++ b/{rel}\n" + "\n".join(f"+{line}" for line in lines)
    patch, truncated = _truncate(patch)
    return FilePatch(path=rel, status=status, patch=patch, additions=len(lines), truncated=truncated)


def _truncate(patch: str) -> tuple[str, bool]:
    if not patch:
        return "", False
    lines = patch.splitlines()
    if len(lines) <= MAX_PATCH_LINES and len(patch.encode("utf-8")) <= MAX_FILE_BYTES:
        return patch, False
    kept: list[str] = []
    size = 0
    for line in lines:
        encoded = (line + "\n").encode("utf-8")
        if len(kept) >= MAX_PATCH_LINES or size + len(encoded) > MAX_FILE_BYTES:
            break
        kept.append(line)
        size += len(encoded)
    kept.append("… 后面已截断")
    return "\n".join(kept), True


def _count_lines(patch: str) -> tuple[int, int]:
    adds = dels = 0
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            adds += 1
        elif line.startswith("-"):
            dels += 1
    return adds, dels


def _normalize_headers(patch: str, path: str, from_path: str | None) -> str:
    if not patch:
        return patch
    lines = patch.splitlines()
    out: list[str] = []
    seen_minus = seen_plus = False
    for line in lines:
        if line.startswith("---") and not seen_minus:
            seen_minus = True
            left = from_path or path
            out.append("--- /dev/null" if "dev/null" in line.replace("\\", "/") else f"--- a/{left}")
            continue
        if line.startswith("+++") and not seen_plus:
            seen_plus = True
            out.append(f"+++ b/{path}")
            continue
        out.append(line)
    return "\n".join(out)


def render_trial_html(trial_id: str, subtitle: str, bundle: DiffBundle) -> str:
    files = bundle.files
    nav = []
    sections = []
    for i, item in enumerate(files):
        label = STATUS_LABEL.get(item.status, item.status)
        extra = f" ← {item.from_path}" if item.from_path else ""
        nav.append(
            f'<a href="#f-{i}"><span class="st st-{_esc(item.status)}">{_esc(label)}</span>'
            f"{_esc(item.path)}{_esc(extra)}</a>"
        )
        meta = []
        if item.additions or item.deletions:
            meta.append(f'<span class="plus">+{item.additions}</span> <span class="minus">−{item.deletions}</span>')
        if item.binary:
            meta.append("二进制")
        if item.truncated:
            meta.append("已截断")
        if item.note:
            meta.append(_esc(item.note))
        body = _render_patch(item.patch) if item.patch and not item.binary else (
            f'<p class="note">{_esc(item.note or "没有文本 diff")}</p>'
        )
        sections.append(
            f'<section id="f-{i}"><h2>{_esc(item.path)}{_esc(extra)} '
            f'<span class="st st-{_esc(item.status)}">{_esc(label)}</span></h2>'
            f'<p class="meta">{" · ".join(meta)}</p>{body}</section>'
        )
    if not files:
        sections.append("<p>这次没有改文件。</p>")
    return _page(
        title=f"改动阅读 · {trial_id}",
        heading="这次改了什么",
        sub=f"{_esc(subtitle)} · {_esc(trial_id)}",
        summary=_summary_line(len(files), bundle.additions, bundle.deletions),
        nav="\n".join(nav),
        main="\n".join(sections),
    )


def render_run_html(run_id: str, items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        n = int(item.get("files") or 0)
        adds = int(item.get("additions") or 0)
        dels = int(item.get("deletions") or 0)
        href = item.get("href") or "#"
        tid = item.get("trial_id") or ""
        rows.append(
            "<tr>"
            f"<td>{_esc(str(item.get('variant_id') or ''))}</td>"
            f"<td>{_esc(str(item.get('cell_id') or ''))}</td>"
            f"<td>{_esc(str(item.get('case_id') or ''))}</td>"
            f"<td>r{item.get('repeat') or 1}</td>"
            f"<td>{n}</td>"
            f'<td><span class="plus">+{adds}</span> <span class="minus">−{dels}</span></td>'
            f'<td><a href="{_esc(href)}">{_esc(tid)}</a></td>'
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>对照/改法</th><th>命令</th><th>用例</th><th>第几次</th><th>文件</th><th>行</th><th>阅读</th>"
        "</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )
    return _page(
        title=f"改动阅读 · {run_id}",
        heading="这次运行的代码改动",
        sub=_esc(run_id),
        summary=f"{len(items)} 次试验",
        nav="",
        main=table,
        index=True,
    )


def _summary_line(n_files: int, adds: int, dels: int) -> str:
    return f"{n_files} 个文件 · <span class=\"plus\">+{adds}</span> / <span class=\"minus\">−{dels}</span>"


def _render_patch(patch: str) -> str:
    lines = []
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("diff ") or line.startswith("index "):
            cls = "hdr"
        elif line.startswith("@@"):
            cls = "hunk"
        elif line.startswith("+"):
            cls = "add"
        elif line.startswith("-"):
            cls = "del"
        else:
            cls = "ctx"
        lines.append(f'<div class="{cls}">{_esc(line) or "&nbsp;"}</div>')
    return '<pre class="diff">' + "\n".join(lines) + "</pre>"


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _page(*, title: str, heading: str, sub: str, summary: str, nav: str, main: str, index: bool = False) -> str:
    aside = f"<aside>{nav}</aside>" if nav else ""
    layout = "index" if index or not nav else "split"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
:root {{ color-scheme: light; }}
body {{ margin:0; font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; color:#1f2328; background:#fff; }}
header {{ padding: 20px 24px 12px; border-bottom:1px solid #d0d7de; }}
header h1 {{ margin:0 0 4px; font-size:20px; }}
header .sub, header .sum {{ color:#656d76; font-size:13px; }}
.split {{ display:flex; align-items:flex-start; }}
aside {{ width:280px; flex-shrink:0; position:sticky; top:0; height:100vh; overflow:auto;
  border-right:1px solid #d0d7de; padding:12px 0; }}
aside a {{ display:block; padding:6px 16px; color:#1f2328; text-decoration:none; font-size:13px; }}
aside a:hover {{ background:#f6f8fa; }}
main {{ flex:1; padding:16px 24px 48px; min-width:0; }}
section {{ margin: 0 0 28px; }}
h2 {{ font-size:15px; margin:0 0 8px; word-break:break-all; }}
.meta, .note {{ color:#656d76; font-size:13px; }}
.st {{ display:inline-block; font-size:11px; padding:1px 6px; border-radius:999px; margin-right:6px; }}
.st-A,.st-U {{ background:#dafbe1; color:#1a7f37; }}
.st-M {{ background:#fff8c5; color:#9a6700; }}
.st-D {{ background:#ffebe9; color:#cf222e; }}
.st-R {{ background:#ddf4ff; color:#0969da; }}
.plus {{ color:#1a7f37; }} .minus {{ color:#cf222e; }}
pre.diff {{ margin:8px 0 0; padding:8px 0; overflow:auto; background:#f6f8fa; border:1px solid #d0d7de;
  border-radius:6px; font:12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }}
pre.diff div {{ padding:0 12px; white-space:pre; }}
.add {{ background:#dafbe1; }} .del {{ background:#ffebe9; }}
.hunk {{ background:#ddf4ff; color:#0969da; }} .hdr {{ color:#656d76; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #d0d7de; vertical-align:top; }}
th {{ font-size:12px; color:#656d76; font-weight:600; }}
a {{ color:#0969da; }}
</style>
</head>
<body>
<header>
<h1>{_esc(heading)}</h1>
<div class="sub">{sub}</div>
<div class="sum">{summary}</div>
</header>
<div class="{layout}">{aside}<main>{main}</main></div>
</body>
</html>
"""
