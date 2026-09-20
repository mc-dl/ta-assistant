"""主入口:
- CLI: python -m src.main --message "xxx" → 打印 JSON
- HTTP: uvicorn src.main:app --port 8765 → POST /process
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from src.handlers import classifier, qa, record, submission
from src.llm.minmax import MinMaxClient
from src.utils.logger import log_event
from src import config


def _strip_forward_prefix(message: str) -> tuple[bool, str]:
    """若消息以任一前缀开头,返回 (True, 剥前缀后的正文);否则 (False, 原文)。"""
    stripped = message.lstrip()  # 容忍前导空格/换行
    for prefix in config.STUDENT_FORWARD_PREFIXES:
        if stripped.startswith(prefix):
            return True, stripped[len(prefix):].strip()
    return False, message


def _safe(fn, tag: str) -> dict[str, Any]:
    """跑一个 Handler,别让它的异常变成"机器人没回话"。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log_event("handler_error", type=tag, error=str(e))
        return {
            "type": "error",
            "reply": f"系统处理出错,请稍后再试。详情:{e}",
        }


def process(message: str) -> dict[str, Any]:
    """统一处理入口:前缀过滤 → 分类 → 分派 Handler → 返回结果。"""
    # 【记录】是**助教的元命令**,必须在学生转发闸门**之前**判断。
    # 顺序反了会怎样:助教发「【记录】问题:…」时不带「【转发学生提问】」前缀,
    # 于是会被下面那个闸门判成"非学生消息"直接丢掉 —— 入口看着在、其实够不着。
    is_record, record_body = record.strip_prefix(message)

    # === 调试日志:把输入原样记录 ===
    log_event(
        "process_input",
        raw=message,
        raw_repr=repr(message),
        length=len(message),
        first_20_codepoints=[hex(ord(c)) for c in message[:20]] if message else [],
        starts_with_any_prefix=any(message.lstrip().startswith(p) for p in config.STUDENT_FORWARD_PREFIXES),
        prefix_used=config.STUDENT_FORWARD_PREFIX,
        is_record=is_record,
    )

    if is_record:
        result = _safe(lambda: record.handle(record_body), "record")
        log_event("reply", type=result.get("type"), reply_len=len(result.get("reply", "")))
        return result

    # 前缀过滤:只有带指定前缀的学生转发才处理
    matched, msg = _strip_forward_prefix(message)
    if not matched:
        return {"type": "skip", "reply": "(非学生消息,未处理)"}

    if not msg:
        return {"type": "skip", "reply": "(消息为空,未处理)"}

    llm = MinMaxClient.from_env()
    msg_type = classifier.classify(msg, llm=llm)
    log_event("classify", message=msg[:200], type=msg_type)

    if msg_type == "submission":
        result = _safe(lambda: submission.handle(msg), msg_type)
    else:  # question 或 unknown 都走答疑
        result = _safe(lambda: qa.handle(msg, llm=llm), msg_type)

    log_event("reply", type=result.get("type"), reply_len=len(result.get("reply", "")))
    return result


# ─── CLI ──────────────────────────────────────────────────────
def cli_main() -> int:
    parser = argparse.ArgumentParser(description="数电助教自动化助手")
    parser.add_argument("--message", "-m", required=True, help="待处理的微信转发消息")
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON 输出")
    args = parser.parse_args()

    result = process(args.message)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 0


# ─── HTTP(可选) ────────────────────────────────────────────
try:
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI(title="ta-assistant")

    class ProcessIn(BaseModel):
        message: str

    @app.post("/process")
    def http_process(payload: ProcessIn) -> dict:
        return process(payload.message)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

except ImportError:
    app = None  # FastAPI 未装时,只跑 CLI


if __name__ == "__main__":
    sys.exit(cli_main())
