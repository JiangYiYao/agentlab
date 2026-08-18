from __future__ import annotations

from pathlib import Path


def find_experiment_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for directory in [cur, *cur.parents]:
        if (directory / "experiment.yaml").is_file():
            return directory
    return None


def resolve_exp_dir(exp: str | None) -> Path:
    if exp:
        path = Path(exp).expanduser().resolve()
        if path.is_file() and path.name == "experiment.yaml":
            return path.parent
        if (path / "experiment.yaml").is_file():
            return path
        raise FileNotFoundError(f"no experiment.yaml under {path}")
    found = find_experiment_root()
    if found is None:
        raise FileNotFoundError("no experiment.yaml in this directory or parents")
    return found
