from __future__ import annotations

from pathlib import Path

from agentlab.judge import JUDGE_PREAMBLE, criteria_section


def test_preamble_mentions_score_json() -> None:
    assert "concern_id" in JUDGE_PREAMBLE
    assert "不要修改文件" in JUDGE_PREAMBLE


def test_criteria_section_slices(tmp_path: Path) -> None:
    (tmp_path / "criteria.md").write_text("# Criteria\n\n## latency\nA\n\n## label-align\nB\n", encoding="utf-8")
    text = criteria_section(tmp_path, "latency")
    assert "A" in text
    assert "label-align" not in text
