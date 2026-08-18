RE_SECTION_CONCLUSION = r"(?m)^#{0,3}\s*结论\s*$"
RE_SECTION_BASIS = r"(?m)^#{0,3}\s*依据\s*$"
RE_SECTION_CHANGE = r"(?m)^#{0,3}\s*改变判断的条件\s*$"
RE_DIRECTION = r"(?:方向)\s*[：:为是]?\s*(偏多|中性|偏空|无法判断)"
RE_ACTION = r"(?:当前)?动作\s*[：:为是]?\s*(介入|等待|回避)"
COUNTERARG_NEEDLES = ["最强反证", "尚未改变结论", "尚未推翻", "为何尚未推翻", "为何尚未改变"]
SECTION_PATTERNS = {
    "结论": RE_SECTION_CONCLUSION,
    "依据": RE_SECTION_BASIS,
    "改变判断的条件": RE_SECTION_CHANGE,
}
