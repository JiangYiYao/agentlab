from __future__ import annotations

from pathlib import Path

from agentlab.adapters.artifact.dir import DirArtifact
from agentlab.schema import Variant


def test_sidecar_copies_skill_not_discovery(tmp_path: Path) -> None:
    root = tmp_path / "exp"
    src = root / "variants" / "baseline"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    dest = tmp_path / "trial" / "outputs" / "program"
    out = DirArtifact().materialize(Variant(id="baseline", role="baseline", path="variants/baseline"), dest, root)
    assert (out / "SKILL.md").is_file()
    assert not (tmp_path / "trial" / ".claude" / "skills").exists()
    assert not (dest.parent.parent / ".agents" / "skills").exists()


def test_inplace_dest_is_project_root(tmp_path: Path) -> None:
    root = tmp_path / "exp"
    src = root / "variants" / "baseline"
    src.mkdir(parents=True)
    (src / "run.py").write_text("print(1)\n", encoding="utf-8")
    project = tmp_path / "project"
    DirArtifact().materialize(Variant(id="baseline", role="baseline", path="variants/baseline"), project, root)
    assert (project / "run.py").is_file()
    assert not (project / ".claude" / "skills").exists()
