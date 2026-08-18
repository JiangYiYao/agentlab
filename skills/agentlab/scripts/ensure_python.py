#!/usr/bin/env python3
"""Resolve a 3.11+ interpreter with pydantic and PyYAML.

Does not pip-install into the system Python. If nothing ready exists,
creates $AGENTLAB_HOME/.venv (default ~/.agentlab/.venv) from python3.12
or python3.11 and installs the two packages there.

Stdout: absolute path of the interpreter.
Stderr: short log. Exit 0 on success, 2 if no 3.11+ can be created.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _version_tuple(py: str) -> tuple[int, int] | None:
    try:
        out = subprocess.check_output(
            [py, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            text=True,
            timeout=20,
        ).strip()
        major, minor = out.split(".", 1)
        return int(major), int(minor)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _has_deps(py: str) -> bool:
    try:
        subprocess.check_call(
            [py, "-c", "import pydantic, yaml"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _home_venv_python() -> Path:
    home = Path(os.environ.get("AGENTLAB_HOME", Path.home() / ".agentlab")).expanduser()
    return home / ".venv" / "bin" / "python"


def _candidates() -> list[str]:
    ordered: list[str] = []
    env = os.environ.get("AGENTLAB_PYTHON")
    if env:
        ordered.append(env)
    home_py = _home_venv_python()
    if home_py.is_file():
        ordered.append(str(home_py))
    for name in ("python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            ordered.append(found)
    # de-dupe, keep order
    seen: set[str] = set()
    out: list[str] = []
    for item in ordered:
        key = str(Path(item).expanduser().resolve()) if Path(item).expanduser().exists() else item
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _good_existing() -> str | None:
    for py in _candidates():
        ver = _version_tuple(py)
        if ver is None or ver < (3, 11):
            continue
        if _has_deps(py):
            return py
    return None


def _bootstrap() -> str:
    py = _home_venv_python()
    venv = py.parent.parent
    py_s = str(py)
    if py.is_file():
        ver = _version_tuple(py_s)
        if ver and ver >= (3, 11) and _has_deps(py_s):
            return py_s

    creator = None
    for name in ("python3.12", "python3.11"):
        found = shutil.which(name)
        if found and (_version_tuple(found) or (0, 0)) >= (3, 11):
            creator = found
            break
    if creator is None:
        raise SystemExit(
            "no Python 3.11+ on PATH (tried python3.12, python3.11). "
            "Install one, or set AGENTLAB_PYTHON to a 3.11+ interpreter."
        )

    venv.parent.mkdir(parents=True, exist_ok=True)
    _log(f"creating {venv} with {creator}")
    subprocess.check_call([creator, "-m", "venv", str(venv)])
    subprocess.check_call([py_s, "-m", "pip", "install", "-q", "pydantic>=2.6", "pyyaml>=6.0"])
    if not _has_deps(py_s):
        raise SystemExit(f"venv created at {venv} but pydantic/yaml still missing")
    return py_s


def main() -> int:
    existing = _good_existing()
    if existing:
        print(existing)
        return 0
    try:
        print(_bootstrap())
    except subprocess.CalledProcessError as exc:
        print(f"failed to prepare ~/.agentlab/.venv: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
