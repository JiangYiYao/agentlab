from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def hash_snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    if not root.is_dir():
        return snap
    for file in root.rglob("*"):
        if not file.is_file() or ".git" in file.parts:
            continue
        snap[file.relative_to(root).as_posix()] = file_digest(file)
    return snap


def git_roots(project_root: Path) -> list[Path]:
    if not project_root.is_dir():
        return []
    roots: list[Path] = []
    seen: set[Path] = set()
    candidates = [project_root, *project_root.rglob(".git")]
    for item in candidates:
        repo = item.parent if item.name == ".git" else item
        if not (repo / ".git").exists():
            continue
        resolved = repo.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)
    roots.sort(key=lambda p: len(p.parts))
    return roots


def collect_changes(project_root: Path, snap: dict[str, str] | list[str] | None = None) -> list[dict[str, str]]:
    # Snapshot vs now is what this trial changed. Git status vs HEAD can include
    # dirt that was already there before the trial started.
    hashed = _hash_changes(project_root, snap or {})
    git_items: list[dict[str, str]] = []
    for repo in git_roots(project_root):
        git_items.extend(_git_status(repo, project_root))
    if not git_items:
        return hashed
    by_path = {item["path"]: item for item in git_items}
    renamed_from = {git["from"] for git in git_items if git.get("status") == "R" and git.get("from")}
    out: list[dict[str, str]] = []
    for item in hashed:
        if item["status"] == "D" and item["path"] in renamed_from:
            continue
        git = by_path.get(item["path"])
        if item["status"] == "A" and git and git.get("untracked"):
            out.append({**item, "status": "U"})
        elif git and git["status"] == "R":
            out.append(git)
        else:
            out.append(item)
    return _dedupe(out)


def _git_status(repo: Path, project_root: Path) -> list[dict[str, str]]:
    try:
        text = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "-uall"],
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    changes: list[dict[str, str]] = []
    for line in text.splitlines():
        if len(line) < 4 or line.startswith("!!"):
            continue
        rest = line[3:]
        if " -> " in rest:
            old, new = rest.split(" -> ", 1)
            rel = _rel(repo / new.strip().strip('"'), project_root)
            rel_from = _rel(repo / old.strip().strip('"'), project_root)
            if rel:
                changes.append({"path": rel, "status": "R", "from": rel_from or old.strip().strip('"')})
            continue
        path = rest.strip().strip('"')
        rel = _rel(repo / path, project_root)
        if not rel:
            continue
        xy = line[:2]
        if xy == "??":
            changes.append({"path": rel, "status": "U", "untracked": "true"})
            continue
        if "D" in xy:
            status = "D"
        elif "A" in xy:
            status = "A"
        else:
            status = "M"
        changes.append({"path": rel, "status": status})
    return changes


def _hash_changes(project_root: Path, snap: dict[str, str] | list[str]) -> list[dict[str, str]]:
    now = hash_snapshot(project_root)
    if isinstance(snap, list):
        old_paths = set(snap)
        old_hashes: dict[str, str] = {}
    else:
        old_paths = set(snap)
        old_hashes = dict(snap)
    changes: list[dict[str, str]] = []
    for path in sorted(now.keys() - old_paths):
        changes.append({"path": path, "status": "A"})
    for path in sorted(old_paths - now.keys()):
        changes.append({"path": path, "status": "D"})
    for path in sorted(now.keys() & old_paths):
        if old_hashes and old_hashes.get(path) and old_hashes[path] != now[path]:
            changes.append({"path": path, "status": "M"})
        elif not old_hashes:
            continue
    return _pair_renames(changes, old_hashes, now)


def _pair_renames(
    changes: list[dict[str, str]], old_hashes: dict[str, str], now: dict[str, str]
) -> list[dict[str, str]]:
    if not old_hashes:
        return changes
    deleted = [c for c in changes if c["status"] == "D"]
    added = [c for c in changes if c["status"] == "A"]
    used_add: set[str] = set()
    used_del: set[str] = set()
    renamed: list[dict[str, str]] = []
    for d in deleted:
        digest = old_hashes.get(d["path"])
        if not digest:
            continue
        match = next((a for a in added if a["path"] not in used_add and now.get(a["path"]) == digest), None)
        if not match:
            continue
        used_add.add(match["path"])
        used_del.add(d["path"])
        renamed.append({"path": match["path"], "status": "R", "from": d["path"]})
    keep = [c for c in changes if c["path"] not in used_add and c["path"] not in used_del]
    return keep + renamed


def _rel(path: Path, project_root: Path) -> str | None:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None


def _dedupe(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for item in changes:
        key = (item["path"], item["status"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
