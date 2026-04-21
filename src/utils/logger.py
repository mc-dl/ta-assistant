"""JSON Lines 日志:每次请求记一行,方便 grep 和统计。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src import config


def log_event(event: str, **fields) -> None:
    """追加一行 JSON 到本月日志文件。失败静默,不影响主流程。"""
    try:
        path: Path = config.log_path_for_month()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "event": event,
                **fields,
            },
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
