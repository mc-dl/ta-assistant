#!/usr/bin/env python3
"""OpenClaw 调用入口:stdout 只输出一行 JSON,其他全走 stderr。"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 抑制 jieba 日志(它默认打到 stderr,不过显式关掉更干净)
logging.getLogger("jieba").setLevel(logging.ERROR)
os.environ.setdefault("JIEBA_LOG_LEVEL", "ERROR")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", "-m", required=True)
    args = parser.parse_args()

    # 关键:在 process() 执行期间,把 stdout 暂时指向 stderr,
    # 任何模块里的 print() 都不会污染我们最终的 JSON 输出
    real_stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from src.main import process
            result = process(args.message)
    except SystemExit:
        # argparse 等可能抛 SystemExit,透传
        raise
    except BaseException as e:  # noqa: BLE001
        result = {
            "type": "error",
            "reply": f"系统错误:{type(e).__name__}: {e}",
            "trace": traceback.format_exc(),
        }

    # 恢复 stdout,写唯一一行 JSON
    try:
        real_stdout.write(json.dumps(result, ensure_ascii=False))
        real_stdout.write("\n")
        real_stdout.flush()
    except Exception as e:  # 写 JSON 失败时兜底
        real_stdout.write(json.dumps(
            {"type": "error", "reply": f"JSON 序列化失败: {e}"},
            ensure_ascii=False,
        ) + "\n")
        real_stdout.flush()

    # exit code:type=error 返 1,其余返 0
    return 1 if result.get("type") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
