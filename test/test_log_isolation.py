"""单测不许写进生产日志目录。

**为什么单独给这两条断言开一个文件。**
`data/logs/YYYY-MM.log` 是这个项目**唯一**能回答"学生到底在问什么"的数据源 ——
FAQ 该补哪条、资料库哪块内容缺、事务题占多大比例,全都只能从它推。
而它此前是被单测**一直往里写**的:2026-09-20 实测,543 条 `qa_kind` 问句里
521 条(95.9%)的原文恰好等于 `test/` 里的字符串字面量,连"高频缺口前三名"
(随便问问 46 次 / 1.36 怎么做 14 次 / 补交怎么办? 13 次)都是测试用例。

它属于那种"坏了不会报错"的东西:日志多几行没人看得出来,但一旦要拿它做判断,
结论就已经错了,而且没法回溯剔除。所以明确钉两条 ——
**一条钉"没写进生产目录",一条钉"日志本身还在正常工作"**。
只有前者的话,把 `log_event` 改成空函数也能过。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src import config
from src.utils.logger import log_event

REAL_LOGS_DIR = config.PROJECT_ROOT / "data" / "logs"
ROOT = Path(__file__).resolve().parent.parent


def _snapshot() -> dict[str, int]:
    return {p.name: p.stat().st_size for p in REAL_LOGS_DIR.glob("*") if p.is_file()}


def test_logs_dir_is_redirected_away_from_the_project():
    """`config.LOGS_DIR` 在单测里不该指向项目内的 `data/logs`。

    这条是 `conftest.isolated_logs` 的守卫:谁把它删了,这里立刻红。
    """
    assert config.LOGS_DIR != REAL_LOGS_DIR, \
        "单测期间 LOGS_DIR 必须被 conftest.isolated_logs 指走"


def test_writing_a_log_event_does_not_touch_the_production_dir():
    """真写一条日志,生产目录必须**一个字节都没变**。

    只看 `config.LOGS_DIR` 这个值不够:值对了但 `log_event` 走别的路径写
    (比如自己拼了绝对路径)照样能污染,所以这里比的是字节数。
    """
    before = _snapshot()
    log_event("qa_kind", message="单测写入探针", kind="admin", faq_hit=False)
    assert _snapshot() == before, "单测往生产日志目录里写了东西"


def test_log_event_still_actually_writes():
    """但日志本身要照常工作 —— 否则上面那条靠"日志坏了"也能通过。

    这条保证日志写进了**被指过去的那个**目录,而且内容完整。
    """
    log_event("qa_kind", message="探针", kind="admin", faq_hit=False)

    path = config.LOGS_DIR / f"{datetime.now().strftime('%Y-%m')}.log"
    assert path.exists(), "日志应当写进被指过去的目录"
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any('"event": "qa_kind"' in l and '"message": "探针"' in l for l in lines)


def test_bridge_subprocess_also_writes_to_the_redirected_dir(offline_env):
    """**子进程那条路必须单独守。**

    `subprocess` 起的 bridge 是另一份 Python 进程:`isolated_logs` 改的是父进程的
    `config.LOGS_DIR` 对象,子进程 import 时自己算一遍,改不到它 ——
    所以只能靠 `TA_LOGS_DIR` 环境变量(见 `offline_env`)。
    实测泄露的一轮:跑完整套单测,生产日志多了 8 行,其中 `qa_kind` +
    `process_input` + `classify` + `reply` 全都有。

    这条断言分两半,缺一不可:
      · bridge 真的跑通了(否则"没写日志"只是因为进程崩了);
      · 生产目录一个字节没变。
    """
    before = _snapshot()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "openclaw_bridge.py"),
         "--message", "【转发学生提问】Proteus 在哪下载?"],
        capture_output=True, text=True, timeout=60, env=offline_env,
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"bridge 没跑通,stdout={result.stdout!r} stderr={result.stderr!r}"
    json.loads(lines[0])

    assert _snapshot() == before, "子进程往生产日志目录里写了东西"
