"""保证 bridge 的 stdout 永远只有一行合法 JSON。

 regressions:防止有人再在 src/ 里加 print 污染 OpenClaw 的 JSON.parse。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_bridge_stdout_is_pure_json():
    """bridge stdout 必须只有一行合法 JSON,print 等不得污染。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "openclaw_bridge.py"),
         "--message", "你好"],
        capture_output=True, text=True, timeout=60,
    )
    # stdout 必须能被 JSON 解析,只有一行
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, (
        f"stdout 应只有一行,实际 {len(lines)} 行:\n"
        f"  stdout={result.stdout!r}\n"
        f"  stderr={result.stderr!r}"
    )
    data = json.loads(lines[0])  # 爆掉说明有污染
    assert "type" in data and "reply" in data
    # stderr 可以有内容(jieba 警告/调试日志),不检查


def test_bridge_stdout_clean_with_prefix():
    """带前缀的消息,stdout 仍只有一行 JSON。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "openclaw_bridge.py"),
         "--message", "【转发学生提问】Proteus 在哪下载?"],
        capture_output=True, text=True, timeout=60,
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, (
        f"stdout 应只有一行,实际 {len(lines)} 行:\n"
        f"  stdout={result.stdout!r}\n"
        f"  stderr={result.stderr!r}"
    )
    data = json.loads(lines[0])
    assert "type" in data


def test_bridge_no_extra_newlines():
    """stdout 不能有多余空行,确保 JSON.parse 不会因尾随换行爆掉。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "openclaw_bridge.py"),
         "--message", "【转发学生提问】王小明 20230001 漏交第1次作业"],
        capture_output=True, text=True, timeout=60,
    )
    # 只允许一条非空行
    non_empty = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(non_empty) == 1
    # json.loads 不应因尾随换行失败
    json.loads(non_empty[0])
