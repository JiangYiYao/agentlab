from __future__ import annotations

from agentlab.adapters.evaluator.builtin import _counterarg, _extract_labels, _section_present
from agentlab.schema import Concern, Measure

SAMPLE = """# 报告

## 结论

方向：偏多
当前动作是等待

## 依据

基本面尚可。最强反证是估值偏贵，尚未改变结论，因为动量仍在。

## 改变判断的条件

跌破均线。
"""


def test_label_and_counterarg_on_real_shape() -> None:
    extracted = _extract_labels(SAMPLE, None)
    assert extracted["direction"] == "偏多"
    assert extracted["action"] == "等待"
    concern = Concern(
        id="counterarg-inline",
        intent="x",
        role="metric",
        measure=Measure(type="counterarg_inline", source="unused"),
    )
    # bypass file by monkeypatching resolve? call internals
    from agentlab.adapters.evaluator import builtin as b

    class T:
        pass

    def fake_resolve(*_a, **_k):
        return SAMPLE

    orig = b.resolve_report_text
    b.resolve_report_text = fake_resolve
    try:
        ok, ev = _counterarg(T(), concern, {})  # type: ignore[arg-type]
        assert ok, ev
        sec = Concern(
            id="sections-present",
            intent="x",
            role="metric",
            measure=Measure(type="section_present", source="u", must_include=["结论", "依据", "改变判断的条件"]),
        )
        ok2, ev2 = _section_present(T(), sec, {})  # type: ignore[arg-type]
        assert ok2, ev2
    finally:
        b.resolve_report_text = orig
