from __future__ import annotations

from pathlib import Path

WRAPPER_TEMPLATE = r'''#!/usr/bin/env python3
import importlib
import sys
from pathlib import Path

_HOOK = Path("__AGENTLAB_HOOK_DIR__") / "scutio_hook.py"
sys.path.insert(0, str(_HOOK.parent))
from scutio_hook import attach

script = Path(sys.argv[1]).resolve()
sys.argv = [str(script), *sys.argv[2:]]
sys.path.insert(0, str(script.parent))
mod = importlib.import_module(script.stem)
attach(mod, script)
raise SystemExit(mod.main(sys.argv[1:]))
'''


def render_wrapper(hook_dir: Path) -> str:
    return WRAPPER_TEMPLATE.replace("__AGENTLAB_HOOK_DIR__", str(hook_dir))
