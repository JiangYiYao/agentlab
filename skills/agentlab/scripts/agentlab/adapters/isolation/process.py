from __future__ import annotations

import os
import signal
import time
from typing import Any


def _signal(pid: int, sig: int) -> str:
    """Return 'gone', 'alive', or 'unknown' without raising."""
    try:
        os.killpg(pid, sig)
        return "alive"
    except ProcessLookupError:
        return "gone"
    except (PermissionError, OSError):
        try:
            os.kill(pid, sig)
            return "alive"
        except ProcessLookupError:
            return "gone"
        except (PermissionError, OSError):
            return "unknown"


def kill_process_group(pid: int, *, grace_s: float = 10.0) -> None:
    if pid <= 0:
        return
    if _signal(pid, signal.SIGTERM) == "gone":
        return
    deadline = time.time() + grace_s
    while time.time() < deadline:
        if _signal(pid, 0) == "gone":
            return
        time.sleep(0.05)
    _signal(pid, signal.SIGKILL)


def start_session_kwargs() -> dict[str, Any]:
    return {"start_new_session": True}
