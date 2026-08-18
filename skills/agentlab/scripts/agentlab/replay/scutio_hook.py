"""Stdlib-only hook. Must not import agentlab."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable


def wrap_fn(obj: Any, name: str, fn: Callable | None = None, *, after: Callable | None = None, replace: Callable | None = None) -> None:
    original = getattr(obj, name, None)
    if original is None:
        return
    if replace is not None:
        setattr(obj, name, replace)
        return

    def wrapped(*args, **kwargs):
        result = (fn(*args, **kwargs) if fn is not None else original(*args, **kwargs))
        if after is not None:
            after(result)
        return result

    setattr(obj, name, wrapped)


def _lock() -> dict:
    path = os.environ.get("AGENTLAB_REPLAY_LOCK")
    if not path:
        raise RuntimeError("AGENTLAB_REPLAY_LOCK unset")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def replay_market_clock(*_a, **_k) -> dict:
    data = _lock()
    lock = data["lock"]
    return {
        "as_of": lock["as_of"],
        "observed_at": data.get("clock") or f"{lock['as_of']}T15:00:00+08:00",
        "timezone": "Asia/Shanghai",
    }


def replay_macro_clock(focus=None, now=None):
    return replay_market_clock()


def inject_freeze_into_new_run(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        return
    data = _lock()
    lock = data["lock"]
    freeze = Path(data["freeze_dir"])
    run_dir = Path(manifest.get("run_dir") or manifest.get("new_run_dir") or "")
    if not run_dir:
        snap = Path(manifest.get("snapshot_path") or "")
        run_dir = snap.parent if snap else Path(".")
    snap_src = freeze / "snapshot.json"
    snap_dst = Path(manifest.get("snapshot_path") or (run_dir / "snapshot.json"))
    snap_dst.parent.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if snap_src.is_file():
        payload = json.loads(snap_src.read_text(encoding="utf-8"))
        payload["run_id"] = manifest.get("run_id")
        payload["as_of"] = lock["as_of"]
        snap_dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        copied.append(str(snap_dst))
    packets = freeze / "packets"
    if packets.is_dir():
        dest_packets = run_dir / "packets"
        dest_packets.mkdir(parents=True, exist_ok=True)
        for src in packets.iterdir():
            if src.is_file():
                body = src.read_text(encoding="utf-8")
                (dest_packets / src.name).write_text(body, encoding="utf-8")
                copied.append(str(dest_packets / src.name))
    news = data.get("news")
    cassette = data.get("cassette_path")
    news_path = manifest.get("news_memo_path")
    if news == "cassette" and cassette and news_path:
        Path(news_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cassette, news_path)
        copied.append(str(news_path))
    macro_src = freeze / "macro_context.json"
    macro_dst = manifest.get("macro_context_path")
    if macro_src.is_file() and macro_dst:
        Path(macro_dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(macro_src, macro_dst)
        copied.append(str(macro_dst))
    trial_out = Path(os.environ["AGENTLAB_TRIAL_OUT"])
    replay = {
        "new_run_dir": str(run_dir),
        "new_run_id": manifest.get("run_id"),
        "freeze_dir": str(freeze),
        "files_copied": copied,
        "prepare_script": os.environ.get("AGENTLAB_PREPARE_SCRIPT", ""),
    }
    (trial_out / "replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replay_collect_snapshot(*_a, **_k) -> dict:
    trial_out = Path(os.environ["AGENTLAB_TRIAL_OUT"])
    replay = json.loads((trial_out / "replay.json").read_text(encoding="utf-8"))
    snap = Path(replay["new_run_dir"]) / "snapshot.json"
    return json.loads(snap.read_text(encoding="utf-8"))


def replay_collect_macro(*_a, **_k) -> dict:
    data = _lock()
    src = Path(data["freeze_dir"]) / "macro_context.json"
    if src.is_file():
        return json.loads(src.read_text(encoding="utf-8"))
    return {"skipped": True}


def attach(mod, script) -> None:
    script_path = Path(script)
    try:
        import _snapshot.market as mkt  # type: ignore

        wrap_fn(mkt, "market_clock", replay_market_clock)
    except Exception:
        pass
    if hasattr(mod, "market_clock"):
        wrap_fn(mod, "market_clock", replay_market_clock)
    if hasattr(mod, "_market_clock"):
        wrap_fn(mod, "_market_clock", replay_market_clock)
    if script_path.name == "prepare_run.py":
        os.environ["AGENTLAB_PREPARE_SCRIPT"] = str(script_path.resolve())
        wrap_fn(mod, "prepare", after=inject_freeze_into_new_run)
    if script_path.name == "collect_snapshot.py":
        wrap_fn(mod, "collect", replace=replay_collect_snapshot)
    if script_path.name == "collect_macro.py":
        wrap_fn(mod, "_macro_clock", replay_macro_clock)
        wrap_fn(mod, "collect_macro", replace=replay_collect_macro)
