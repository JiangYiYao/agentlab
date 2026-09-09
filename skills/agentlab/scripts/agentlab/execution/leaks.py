from __future__ import annotations

import hashlib
from pathlib import Path

FORBIDDEN_WRITES = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / ".codex" / "skills",
    Path.home() / ".opencode" / "skills",
]

def fingerprint_tree(path: Path) -> str:
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file.relative_to(path).as_posix().encode()
        digest.update(rel)
        digest.update(file.read_bytes())
    return digest.hexdigest()


def snapshot_forbidden_paths() -> dict[str, str]:
    return {str(p): fingerprint_tree(p) for p in FORBIDDEN_WRITES}


def leak_scores(before: dict[str, str], after: dict[str, str]) -> bool:
    return any(after.get(k) != v for k, v in before.items())


def forbidden_executed_trees(source_path: str | None, experiment_root: Path) -> list[Path]:
    out: list[Path] = []
    if source_path:
        src = Path(source_path).expanduser().resolve()
        try:
            src.relative_to(experiment_root.resolve())
        except ValueError:
            out.append(src)
    return out


def path_in_trees(path: Path, trees: list[Path]) -> bool:
    resolved = path.resolve()
    for tree in trees:
        try:
            resolved.relative_to(tree.resolve())
            return True
        except ValueError:
            continue
    return False
