from __future__ import annotations

from pathlib import Path

from agentlab.adapters.evaluator.builtin import _in_scope, _line_present


def test_in_scope_exclude_cache() -> None:
    assert _in_scope("src/a.py", None, ["**/.agents/**"]) is True
    assert _in_scope(".agents/hooks/x.json", None, ["**/.agents/**", ".agents/**"]) is False
    assert _in_scope("src/a.py", ["src/**"], None) is True
    assert _in_scope("docs/a.md", ["src/**"], None) is False


def test_line_present_skips_excluded(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    (root / ".agents" / "cache.json").write_text("fold_max_count\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("ok\n", encoding="utf-8")
    assert _line_present(root, "fold_max_count", None, ["**/.agents/**", ".agents/**"]) is False
    (root / "src" / "a.py").write_text("fold_max_count\n", encoding="utf-8")
    assert _line_present(root, "fold_max_count", None, ["**/.agents/**", ".agents/**"]) is True
