from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "skills" / "agentlab" / "scripts" / "agentlab" / "replay" / "scutio_hook.py"
MINI = Path(__file__).resolve().parents[2] / "examples" / "case2" / "variants" / "baseline" / "scripts"
FREEZE = Path(__file__).resolve().parents[2] / "examples" / "case2" / "fixtures" / "synthetic" / "DEMO_合成票" / "freeze"

pytestmark = pytest.mark.skipif(not MINI.is_dir() or not FREEZE.is_dir(), reason="examples/case2 is local-only")
WRAPPER = Path(__file__).resolve().parents[2] / "skills" / "agentlab" / "scripts" / "agentlab" / "replay" / "wrapper_src.py"


def _write_lock(tmp: Path, home: Path) -> Path:
    lock = {
        "lock": {"code": "600000", "name": "合成票", "as_of": "2024-01-15", "horizon": "weeks"},
        "clock": "2024-01-15T15:00:00+08:00",
        "freeze_dir": str(FREEZE),
        "cassette_path": str(FREEZE / "analysis" / "analysts" / "events_news.md"),
        "news": "cassette",
    }
    path = tmp / "replay.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def test_prepare_rejects_stale_as_of_without_hook(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["SCUTIO_HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        [sys.executable, str(MINI / "prepare_run.py"), "600000", "--as-of", "2024-01-15"],
        cwd=str(MINI),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0


def test_wrapper_prepare_injects_new_run(tmp_path: Path) -> None:
    from agentlab.replay.wrapper_src import render_wrapper

    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    hook_dir.joinpath("scutio_hook.py").write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper = tmp_path / "scutio-python-replay"
    wrapper.write_text(render_wrapper(hook_dir), encoding="utf-8")
    wrapper.chmod(0o755)
    home = tmp_path / "scutio-home"
    home.mkdir()
    trial_out = tmp_path / "out"
    trial_out.mkdir()
    lock = _write_lock(tmp_path, home)
    env = os.environ.copy()
    env.update(
        {
            "SCUTIO_HOME": str(home),
            "AGENTLAB_TRIAL_OUT": str(trial_out),
            "AGENTLAB_REPLAY_LOCK": str(lock),
        }
    )
    proc = subprocess.run(
        [sys.executable, str(wrapper), str(MINI / "prepare_run.py"), "600000", "--name", "合成票", "--as-of", "2024-01-15"],
        cwd=str(MINI),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    replay = json.loads((trial_out / "replay.json").read_text(encoding="utf-8"))
    assert Path(replay["new_run_dir"]).is_dir()
    assert Path(replay["new_run_dir"]).resolve() != FREEZE.resolve()
    snap = json.loads((Path(replay["new_run_dir"]) / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["run_id"] == replay["new_run_id"]
    news = Path(replay["new_run_dir"]) / "analysis" / "analysts" / "events_news.md"
    assert news.is_file()


def test_collect_snapshot_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlab.replay.wrapper_src import render_wrapper

    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    hook_dir.joinpath("scutio_hook.py").write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper = tmp_path / "wrap"
    wrapper.write_text(render_wrapper(hook_dir), encoding="utf-8")
    wrapper.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    trial_out = tmp_path / "out"
    trial_out.mkdir()
    lock = _write_lock(tmp_path, home)
    env = os.environ.copy()
    env.update(
        {
            "SCUTIO_HOME": str(home),
            "AGENTLAB_TRIAL_OUT": str(trial_out),
            "AGENTLAB_REPLAY_LOCK": str(lock),
        }
    )
    prep = subprocess.run(
        [sys.executable, str(wrapper), str(MINI / "prepare_run.py"), "600000", "--as-of", "2024-01-15", "--name", "合成票"],
        cwd=str(MINI),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    manifest = json.loads(prep.stdout)
    run_json = Path(manifest["run_dir"]) / "run.json"

    def boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", boom)
    proc = subprocess.run(
        [sys.executable, str(wrapper), str(MINI / "collect_snapshot.py"), "--prepared-run", str(run_json)],
        cwd=str(MINI),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
