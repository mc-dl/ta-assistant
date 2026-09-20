"""单测不许碰助教本机的**花名册**与**补交表**。

**为什么单独开一个文件。**
这和 `test_log_isolation.py` 是同一类事故的第二次发作,但更重:
`~/ykt_questions/数字电路与逻辑设计实验（一）补交表.xlsx` 里躺着 **398 行**,
而全部 398 行只包含 **1 个学号、1 个姓名、4 种原文**,四种原文逐字出现在 `test/` 里
时间戳集中在反复跑全量测试的那几天。**没有一条是学生发的。**

危害比日志那次更直接:
  · 那是**要交给助教登记的正式记录**,混着 398 行假数据,真补交根本认不出来;
  · 它还**盖住了真故障** —— 398 行校验状态全是「名单缺失」,和"花名册从来没
    读出来过"长得一模一样,那个 bug 因此被掩护了很久。

所以这里钉三条:**没写进真文件** / **文件本身还在正常工作** / **子进程那条路也堵上了**。
只有第一条的话,把 `append_submission_row` 改成空函数也能过。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src import config
from src.handlers import submission

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = config.WINDOWS_ROOT

# 这句就是历史上把补交表写脏的那句(test_bridge_stdout_clean.py 的第三个用例)
POLLUTING_MESSAGE = "【转发学生提问】王小明 20230001 漏交第1次作业"


def _snapshot() -> dict[str, int]:
    """数据目录下所有 xlsx 的「相对路径 → 字节数」。

    只看 `config.SUBMISSION_TABLE_PATH` 这个值不够:值对了但某处自己拼了绝对路径
    照样能写脏,所以比的是**真实字节**。花名册也一起看 —— 它是只读的,
    跑测试不该让它变一个字节。
    """
    if not DATA_ROOT.exists():
        return {}
    return {
        str(p.relative_to(DATA_ROOT)): p.stat().st_size
        for p in DATA_ROOT.rglob("*.xlsx")
        if p.is_file()
    }


def test_roster_and_table_are_redirected_away_from_real_data():
    """`conftest.isolated_submission_table` 的守卫:谁把它删了,这里立刻红。"""
    assert config.SUBMISSION_TABLE_PATH != DATA_ROOT / "数字电路与逻辑设计实验（一）补交表.xlsx"
    assert config.GRADEBOOK_PATH != DATA_ROOT / "花名册" / "电路基础理论课_学生名单.xlsx"


def test_submission_handle_does_not_touch_real_data():
    """真跑一次补交登记,生产数据目录必须**一个字节都没变**。"""
    before = _snapshot()
    result = submission.handle(POLLUTING_MESSAGE)
    assert result["type"] == "submission"
    assert _snapshot() == before, "单测往助教的生产数据目录里写了东西"


def test_submission_still_actually_writes(isolated_submission_table):
    """但补交登记要照常落盘 —— 否则上面那条靠"功能坏了"也能通过。

    这里同时钉住一件事:名单读不出来时,补交**仍然登记成功**,
    只是校验状态写着「名单缺失」。这是刻意的降级(见 excel_ops.load_roster 的说明),
    不是 bug —— 但它正是这个 bug 藏了几个月的原因,所以写在测试里当证据。
    """
    submission.handle(POLLUTING_MESSAGE)
    table = Path(isolated_submission_table["table"])
    assert table.exists(), "补交记录应当写进被指过去的那个表"

    from src.utils.excel_ops import load_submission_history
    rows = load_submission_history(table)
    assert len(rows) == 1
    assert rows[0]["校验状态"] == "名单缺失"
    assert rows[0]["学号"] == "20230001"


def test_bridge_subprocess_with_submission_message_does_not_touch_real_data(
    offline_env, isolated_submission_table
):
    """**子进程那条路要单独守。**

    `subprocess` 起的 bridge 是另一份 Python 进程:`isolated_submission_table`
    改的是父进程的 `config` 对象,子进程 import 时自己算一遍,改不到它 ——
    只能靠 `SUBMISSION_TABLE` / `ROSTER_PATH` 环境变量(见 `offline_env`)。
    历史上 398 行就是这么来的,而其中一多半出自这个用例的前身。

    两半缺一不可:bridge 真的跑通了(否则"没写"只是因为进程崩了);
    生产目录一个字节没变。
    """
    before = _snapshot()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "openclaw_bridge.py"),
         "--message", POLLUTING_MESSAGE],
        capture_output=True, text=True, timeout=60, env=offline_env,
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"bridge 没跑通,stdout={result.stdout!r} stderr={result.stderr!r}"
    assert json.loads(lines[0])["type"] == "submission"

    assert _snapshot() == before, "子进程往助教的生产数据目录里写了东西"
    # 而且它确实写到了被指过去的那个表里(证明拦截不是因为写入失败)
    assert Path(isolated_submission_table["table"]).exists()
