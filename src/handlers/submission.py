"""补交登记 Handler:从消息中抽取学号/姓名/作业次数,写入补交表。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src import config
from src.utils import excel_ops

STUDENT_ID_RE = re.compile(r"\b(\d{8})\b")
CHINESE_NAME_RE = re.compile(r"[\u4e00-\u9fa5]{2,4}")
HW_NUMBER_PATTERNS = [
    re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*[次章]"),
    re.compile(r"作业\s*([一二三四五六七八九十\d]+)"),
    re.compile(r"实验\s*([一二三四五六七八九十\d]+)"),
    re.compile(r"chapter\s*(\d+)", re.IGNORECASE),
    re.compile(r"hw\s*(\d+)", re.IGNORECASE),
]

# 姓名抽取时要过滤掉的"看起来像姓名但不是"的词
NAME_STOPWORDS = {
    "老师", "助教", "学长", "学姐", "同学", "学生",
    "数电", "数字", "电路", "实验", "作业", "补交",
    "漏交", "没交", "昨天", "今天", "前天", "雨课堂",
    "中山大学", "X 老师", "张三",  # 也可以保留,当学生真叫这个时不会错过
    # 补交消息中的高频非姓名词
    "我作", "我作漏", "作业漏", "作业没", "作业忘",
    "没交", "漏提交", "没提交", "忘提交",
    # 常见问候语/客套话
    "老师您好", "您好", "学长好", "助教好",
}
NAME_STOPWORDS.discard("张三")  # 保留,让"张三"不进入 stopwords 过滤(仍受长度规则约束)

CN_NUM_MAP = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
}


@dataclass
class SubmissionInfo:
    student_id: Optional[str]
    name: Optional[str]
    hw_number: Optional[str]
    raw_message: str
    status: str


def extract_student_id(msg: str) -> Optional[str]:
    m = STUDENT_ID_RE.search(msg)
    return m.group(1) if m else None


def extract_name(msg: str, roster: dict[str, str] | None = None,
                  student_id: Optional[str] = None) -> Optional[str]:
    """优先:花名册反查;其次:消息中的中文姓名模式。"""
    if student_id and roster and student_id in roster:
        return roster[student_id]

    # 显式声明模式:姓名:XXX / 我是 XXX / 我叫 XXX
    for pat in [r"姓名[:：]\s*([\u4e00-\u9fa5]{2,4})",
                r"我[是叫]\s*([\u4e00-\u9fa5]{2,4})"]:
        m = re.search(pat, msg)
        if m:
            name = m.group(1)
            if name not in NAME_STOPWORDS:
                return name

    # 兜底:收集所有 2-4 字连续中文,排除停用词和已知高频非姓名词
    # 从消息右侧往左找(姓名通常在消息后半段)
    candidates = []
    for m in CHINESE_NAME_RE.finditer(msg):
        candidate = m.group(0)
        if candidate in NAME_STOPWORDS:
            continue
        # 检查是否包含"作/业/漏/没/交"等补交相关字(这些不是姓名)
        if any(c in candidate for c in "作没漏交"):
            continue
        if len(candidate) >= 2:
            candidates.append((m.start(), candidate))

    # 从后往前找,姓名通常在消息后半部分
    for start, candidate in reversed(candidates):
        if len(candidate) >= 3:
            return candidate
    # 2 字姓名兜底
    for start, candidate in reversed(candidates):
        if len(candidate) == 2:
            return candidate
    return None


def extract_homework_number(msg: str) -> Optional[str]:
    for pat in HW_NUMBER_PATTERNS:
        m = pat.search(msg)
        if m:
            raw = m.group(1)
            num = CN_NUM_MAP.get(raw, raw)
            return f"第 {num} 次"
    return None


def validate(info: SubmissionInfo, roster: dict[str, str]) -> str:
    if not info.student_id:
        return "缺学号"
    if not roster:
        return "名单缺失"
    if info.student_id not in roster:
        return "学号不在名单"
    if info.name and roster[info.student_id] != info.name:
        return f"姓名不匹配(名单为 {roster[info.student_id]})"
    return "OK"


def handle(msg: str) -> dict:
    """主入口:抽取 → 校验 → 写表 → 返回回复。"""
    try:
        roster = excel_ops.load_roster()
    except FileNotFoundError:
        roster = {}

    sid = extract_student_id(msg)
    name = extract_name(msg, roster=roster, student_id=sid)
    hw = extract_homework_number(msg)

    info = SubmissionInfo(sid, name, hw, msg, status="")
    info.status = validate(info, roster)

    row = {
        "登记时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "学号": sid or "",
        "姓名": name or "",
        "作业次数": hw or "",
        "原始消息": msg,
        "校验状态": info.status,
    }
    excel_ops.ensure_submission_table(config.SUBMISSION_TABLE_PATH)
    excel_ops.append_submission_row(config.SUBMISSION_TABLE_PATH, row)

    reply = _format_reply(info)
    return {"type": "submission", "reply": reply, "row": row}


def _format_reply(info: SubmissionInfo) -> str:
    parts = ["✅ 已登记补交"]
    if info.name:
        parts.append(f"姓名:{info.name}")
    if info.student_id:
        parts.append(f"学号:{info.student_id}")
    if info.hw_number:
        parts.append(f"作业:{info.hw_number}")
    parts.append(f"校验:{info.status}")
    return "  ".join(parts)
