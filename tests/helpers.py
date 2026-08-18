from __future__ import annotations

import hashlib
from pathlib import Path

import yaml


def make_min_exp(root: Path) -> Path:
    """Write a legal minimal experiment under root and return root."""
    (root / "variants" / "baseline").mkdir(parents=True, exist_ok=True)
    (root / "variants" / "treatment").mkdir(parents=True, exist_ok=True)
    (root / "cases" / "smoke").mkdir(parents=True, exist_ok=True)
    (root / "variants" / "baseline" / "SKILL.md").write_text("# baseline\n", encoding="utf-8")
    (root / "variants" / "baseline" / "note.txt").write_text("baseline\n", encoding="utf-8")
    (root / "variants" / "treatment" / "SKILL.md").write_text("# treatment\n", encoding="utf-8")
    (root / "variants" / "treatment" / "note.txt").write_text("treatment\n", encoding="utf-8")
    (root / "cases" / "smoke" / "prompt.md").write_text("ok\n", encoding="utf-8")
    criteria = "# Fixture criteria\n\nThe athlete must exit successfully.\n"
    (root / "criteria.md").write_text(criteria, encoding="utf-8")
    digest = hashlib.sha256(criteria.encode()).hexdigest()
    data = {
        "schema_version": 1,
        "id": "fixture-min",
        "name": "Minimal fixture for schema + brief",
        "artifact": {"type": "dir", "name": "fixture-min", "layout": "sidecar"},
        "criteria": {"path": "criteria.md", "sha256": digest},
        "variants": [
            {"id": "baseline", "role": "baseline", "path": "variants/baseline", "created_by": "import"},
            {
                "id": "treatment",
                "role": "treatment",
                "path": "variants/treatment",
                "parent": "baseline",
                "created_by": "manual",
                "hypothesis": {
                    "change": "Add a greeting note",
                    "bet": "The athlete still exits 0",
                    "hurt": "None in this fixture",
                    "falsify": "true no longer succeeds",
                },
            },
        ],
        "concerns": [
            {
                "id": "smoke",
                "intent": "Fixture objective; command must be resolvable",
                "role": "objective",
                "measure": {"type": "script", "command": ["true"]},
            }
        ],
        "matrix": {"cells": [{"id": "local-cli", "command": ["true"], "prompt": {"mode": "stdin"}}]},
        "cases": [{"id": "smoke", "path": "cases/smoke", "prompt_file": "prompt.md"}],
        "isolation": {"type": "tempdir", "subdir": ".", "inherit_host_identity": True},
        "budget": {
            "max_trials": 8,
            "max_parallel": 1,
            "wall_clock_s": 600,
            "per_trial": {"wall_clock_s": 120},
            "on_exceed": "stop",
        },
        "repetitions": 1,
        "promotion": {"all_cells_must_pass": True, "accept_soft_gates": []},
    }
    (root / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root
