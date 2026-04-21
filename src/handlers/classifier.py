"""消息分类器:把助教转发来的微信消息判为 submission / question / unknown。

规则优先,规则不足时回落到 LLM 兜底。
"""
from __future__ import annotations

import json
import re
from typing import Literal

from src.llm.minmax import MinMaxClient

MessageType = Literal["submission", "question", "unknown"]

SUBMISSION_KEYWORDS = [
    "漏交", "补交", "没交", "忘了交", "忘交",
    "漏了交", "漏提交", "没提交", "忘提交",
    "作业漏", "作业没", "作业忘",
]
QUESTION_KEYWORDS = [
    "?", "?", "怎么", "为什么", "在哪", "哪里",
    "可以吗", "可不可以", "能不能", "能否",
    "是不是", "请问", "求助", "帮忙",
]
STUDENT_ID_RE = re.compile(r"\b\d{8}\b")


def classify(msg: str, llm: MinMaxClient | None = None) -> MessageType:
    """按规则 → LLM 兜底 的顺序分类消息。"""
    msg = (msg or "").strip()
    if not msg:
        return "unknown"

    has_submission_kw = any(kw in msg for kw in SUBMISSION_KEYWORDS)
    has_student_id = bool(STUDENT_ID_RE.search(msg))
    has_question_kw = any(kw in msg for kw in QUESTION_KEYWORDS)

    if has_submission_kw and has_student_id:
        return "submission"
    if has_submission_kw and not has_question_kw:
        # 有"补交"类关键词但没疑问词 → 判为补交
        # (如"我漏交了 20230001 第2次作业"→submission)
        # 有"补交"关键词但没学号,仍倾向补交(后续 handler 会提示缺学号)
        return "submission"
    if has_question_kw:
        return "question"

    # 规则打平,回落到 LLM 兜底
    if llm is not None and llm.available():
        return _llm_fallback(msg, llm)

    return "question"  # 最终兜底:当作问题处理不会破坏数据


def _llm_fallback(msg: str, llm: MinMaxClient) -> MessageType:
    system = (
        "你是一个消息分类器。只能输出 JSON,无任何额外文字。"
        '格式:{"type": "submission" | "question" | "other"}。'
        "submission = 学生在补交作业(通常含姓名、学号、作业次数);"
        "question = 学生在问问题;"
        "other = 其他消息(如闲聊、感谢)。"
    )
    user = f"分类下面这条消息:\n{msg}"
    raw = llm.chat(system, user, temperature=0.0)
    try:
        data = json.loads(raw.strip().strip("` "))
        t = data.get("type", "")
        if t == "submission":
            return "submission"
        if t == "question":
            return "question"
    except Exception:
        pass
    return "unknown"
