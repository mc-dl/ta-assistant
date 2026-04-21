"""统一日志工具:所有 src/ 下的非致命警告/调试信息走这里,不走 stdout。

合同:src/ 下任何模块禁止用 print() 写 stdout,统一用 stderr_log.warn()。
"""
from __future__ import annotations

import sys


def warn(msg: str) -> None:
    """输出一条警告到 stderr(不污染 stdout)。"""
    print(msg, file=sys.stderr)
