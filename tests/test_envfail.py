from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

from agentlab.cli import main
from agentlab.envfail import classify_env_error
from tests.helpers import make_min_exp


def test_classify_trust_and_login() -> None:
    assert classify_env_error("this workspace has not been trusted") == "workspace_untrusted"
    assert classify_env_error("Run Claude Code interactively or set hasTrustDialogAccepted") == (
        "workspace_untrusted"
    )
    assert classify_env_error("error: login required") == "login_required"
    assert classify_env_error("model not found: foo") == "model_not_found"
    assert classify_env_error("[claude-code:unrecognized_model]") is None
    assert classify_env_error("ok, still thinking") is None


def test_stderr_warning_then_success_is_not_env_unusable(tmp_path: Path) -> None:
    script = tmp_path / "warn.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('this workspace has not been trusted\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["matrix"]["cells"][0]["command"] = [sys.executable, str(script)]
    data["budget"] = {"max_trials": 8, "max_parallel": 1}
    data["repetitions"] = 1
    data["variants"] = [data["variants"][0]]
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert main(["run", "--exp", str(dest)]) == 0
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in (dest / "trials").glob("*/meta.json")]
    assert metas
    assert all(m.get("error_code") not in {"env_unusable"} for m in metas)


def test_trust_hang_fails_fast_and_skips_rest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTLAB_ENV_STALL_S", "1")
    hang = tmp_path / "hang.py"
    hang.write_text(
        "import sys, time\n"
        "sys.stderr.write('this workspace has not been trusted\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    dest = make_min_exp(tmp_path / "exp")
    data = yaml.safe_load((dest / "experiment.yaml").read_text(encoding="utf-8"))
    data["matrix"]["cells"][0]["command"] = [sys.executable, str(hang)]
    data["budget"] = {"max_trials": 8, "max_parallel": 1}
    data["repetitions"] = 1
    (dest / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    started = time.time()
    rc = main(["run", "--exp", str(dest), "--gate"])
    elapsed = time.time() - started
    assert rc == 3
    assert elapsed < 12

    metas = [json.loads(p.read_text(encoding="utf-8")) for p in (dest / "trials").glob("*/meta.json")]
    assert metas
    assert any(m.get("error_code") == "env_unusable" for m in metas)
    assert any(m.get("skipped") for m in metas)
