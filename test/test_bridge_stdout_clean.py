"""保证 bridge 的 stdout 永远只有一行合法 JSON。

 regressions:防止有人再在 src/ 里加 print 污染 OpenClaw 的 JSON.parse。

 注意:这三个测试是 `subprocess.run` 起 bridge,**conftest 的
 `offline_http` 打桩管不到子进程**,所以每个都要显式传 `env=offline_env`
 (把两个 API Key 清空)。不传的话,只跑这个文件就会真的去调大模型 ——
 实测过一次:llm.log +x 行、每个 case 多等 2~5 秒。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(message: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "openclaw_bridge.py"),
         "--message", message],
        capture_output=True, text=True, timeout=60, env=env,
    )


def test_bridge_stdout_is_pure_json(offline_env):
    """bridge stdout 必须只有一行合法 JSON,print 等不得污染。"""
    result = _run("你好", offline_env)
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


def test_bridge_stdout_clean_with_prefix(offline_env):
    """带前缀的消息,stdout 仍只有一行 JSON。"""
    result = _run("【转发学生提问】Proteus 在哪下载?", offline_env)
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, (
        f"stdout 应只有一行,实际 {len(lines)} 行:\n"
        f"  stdout={result.stdout!r}\n"
        f"  stderr={result.stderr!r}"
    )
    data = json.loads(lines[0])
    assert "type" in data


def test_bridge_no_extra_newlines(offline_env):
    """stdout 不能有多余空行,确保 JSON.parse 不会因尾随换行爆掉。"""
    result = _run("【转发学生提问】王小明 20230001 漏交第1次作业", offline_env)
    # 只允许一条非空行
    non_empty = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(non_empty) == 1
    # json.loads 不应因尾随换行失败
    json.loads(non_empty[0])
