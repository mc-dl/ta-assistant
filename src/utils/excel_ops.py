"""Excel 操作:花名册读取、补交表读写。"""
from __future__ import annotations

from pathlib import Path

import openpyxl

from src import config
from src.utils.stderr_log import warn as _warn


def load_roster(path: Path | None = None) -> dict[str, str]:
    """从成绩记分册读出 {学号: 姓名}。

    参考 config.ROSTER_HEADER_ROW / ROSTER_DATA_START_ROW 定位数据区。
    找不到文件时返回空字典(不抛异常,让上层按"名单缺失"处理)。
    """
    p = path or config.GRADEBOOK_PATH
    if not p.exists():
        _warn(f"[excel_ops] 花名册不存在: {p}")
        return {}
    wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    ws = wb.active
    roster: dict[str, str] = {}
    for row in ws.iter_rows(
        min_row=config.ROSTER_DATA_START_ROW,
        values_only=True,
    ):
        # 列索引是 0-based,config 里是 1-based
        sid = row[config.ROSTER_COL_ID - 1]
        name = row[config.ROSTER_COL_NAME - 1]
        if sid is None or name is None:
            continue
        sid_str = str(sid).strip()
        name_str = str(name).strip()
        if sid_str.isdigit() and name_str:
            roster[sid_str] = name_str
    return roster


def ensure_submission_table(path: Path) -> None:
    """补交表不存在则自动创建,带标题和表头。"""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "补交记录"
    ws.append(config.SUBMISSION_HEADERS)
    # 简单格式化:表头加粗
    from openpyxl.styles import Font
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    # 列宽
    widths = {"A": 18, "B": 12, "C": 10, "D": 10, "E": 60, "F": 18}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    wb.save(str(path))
    _warn(f"[excel_ops] 新建补交表: {path}")


def append_submission_row(path: Path, row: dict) -> None:
    """追加一行补交记录。读-改-存模式。"""
    ensure_submission_table(path)
    wb = openpyxl.load_workbook(str(path))
    ws = wb.active
    ws.append([row.get(h, "") for h in config.SUBMISSION_HEADERS])
    wb.save(str(path))


def load_submission_history(path: Path | None = None) -> list[dict]:
    """读出补交表全部历史记录(用于重复检测、统计)。"""
    p = path or config.SUBMISSION_TABLE_PATH
    if not p.exists():
        return []
    wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = list(rows[0])
    return [dict(zip(headers, r)) for r in rows[1:] if any(r)]
