from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def new_run_id(root: Path) -> str:
    now = datetime.now(timezone.utc)
    base = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}"
    ident = base
    n = 2
    while (runs_dir(root) / ident).exists():
        ident = f"{base}-{n}"
        n += 1
    return ident


def runs_dir(root: Path) -> Path:
    return root / "runs"


def latest_run_id(root: Path) -> str | None:
    marker = runs_dir(root) / "LATEST"
    if marker.is_file():
        ident = marker.read_text(encoding="utf-8").strip()
        if ident:
            return ident
    if not runs_dir(root).is_dir():
        return None
    dirs = [p.name for p in runs_dir(root).iterdir() if p.is_dir()]
    return max(dirs) if dirs else None


def load_manifest(root: Path, run_id: str) -> dict[str, Any] | None:
    path = runs_dir(root) / run_id / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def planned_ids_for_run(root: Path, run_id: str | None = None) -> list[str] | None:
    ident = run_id or latest_run_id(root)
    if not ident:
        return None
    data = load_manifest(root, ident)
    if not data:
        return None
    planned = data.get("planned")
    if not isinstance(planned, list):
        return None
    return [str(x) for x in planned]


def write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    run_id = str(payload["run_id"])
    dest = runs_dir(root) / run_id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (runs_dir(root) / "LATEST").write_text(run_id + "\n", encoding="utf-8")
    return path


def filter_ids(ids: Iterable[str] | None) -> set[str] | None:
    if ids is None:
        return None
    return set(ids)
