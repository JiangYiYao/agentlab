from __future__ import annotations

import os
import signal
import time
from typing import Any


def kill_process_group(pid: int, *, grace_s: float = 10.0) -> None:
    if pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace_s
    while time.time() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def start_session_kwargs() -> dict[str, Any]:
    return {"start_new_session": True}
