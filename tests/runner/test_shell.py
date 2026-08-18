from __future__ import annotations

from pathlib import Path

from agentlab.schema import Experiment
from agentlab.templates import build_context, resolve_argv


def _min_exp() -> dict:
    return {
        "schema_version": 1,
        "id": "fixture-t",
        "name": "fixture-t",
        "artifact": {"type": "dir", "name": "fixture-t"},
        "criteria": {"path": "criteria.md", "sha256": "ab" * 32},
        "variants": [{"id": "baseline", "role": "baseline", "path": "variants/baseline"}],
        "concerns": [{"id": "smoke", "intent": "x", "role": "objective", "measure": {"type": "script", "command": ["true"]}}],
        "matrix": {"cells": [{"id": "local-cli", "command": ["true"]}]},
        "cases": [{"id": "smoke"}],
        "isolation": {"type": "tempdir"},
        "budget": {"max_trials": 4, "per_trial": {"wall_clock_s": 10}},
    }


def test_resolve_argv_expands_experiment_root(tmp_path: Path) -> None:
    script = tmp_path / "cases" / "_eval" / "fake.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    exp = Experiment.model_validate(_min_exp())
    ctx = build_context(exp=exp, experiment_root=tmp_path)
    argv = resolve_argv(["bash", "${experiment_root}/cases/_eval/fake.sh"], tmp_path, ctx)
    assert argv[1] == str(script.resolve())


def test_resolve_argv_missing_relpath_left_unchanged(tmp_path: Path) -> None:
    exp = Experiment.model_validate(_min_exp())
    ctx = build_context(exp=exp, experiment_root=tmp_path)
    argv = resolve_argv(["bash", "not/created/yet.sh"], tmp_path, ctx)
    assert argv[1] == "not/created/yet.sh"
    (tmp_path / "not" / "created").mkdir(parents=True)
    (tmp_path / "not" / "created" / "yet.sh").write_text("x\n", encoding="utf-8")
    argv2 = resolve_argv(["bash", "not/created/yet.sh"], tmp_path, ctx)
    # first brief-time resolution is what run must keep if it already expanded;
    # second call after file appears may resolve. lock: same input after create
    # should now exist — the design lock is "argv decided at brief stays".
    # Runner expands at run time against experiment_root exists() fallback.
    assert Path(argv2[1]).name == "yet.sh"
