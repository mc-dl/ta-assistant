"""补交表**写到哪儿** —— 路径必须跟着花名册走,而且写不进去时不许说"已登记"。

这个文件钉的是 2026-09-20 那次的直接教训。当时的形状是:

    SUBMISSION_TABLE_PATH = WINDOWS_ROOT / "数字电路与逻辑设计实验（一）补交表.xlsx"

写死的文件名在上学期是对的,这学期换成「电路基础」之后就成了一条**不存在的路径**。
而它坏得很安静:补交照常"成功",只是走进了 `ensure_submission_table` 新建的、
助教永远不会去看的另一张表里 —— 真表一直空着,机器人一直回「✅ 已登记」。

所以这里不测"常量等于某个字符串"(那种断言改一次名字就得跟着改),测的是**关系**:
补交表必须和花名册同目录、由花名册的名字推出来。关系对,换课自动对。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src import config
from src.handlers import submission

ROOT = Path(__file__).resolve().parent.parent

# 会往生产补交表里写脏的那句话(同 test_submission_table_isolation.py)
POLLUTING_MESSAGE = "【转发学生提问】王小明 20230001 漏交第1次作业"


# ─── 命名规则:纯函数,和机器上有没有那份名单无关 ────────────────────

@pytest.mark.parametrize(
    "roster,expect",
    [
        # 这学期实际用的那份(雨课堂导出、助教改名成"学生名单")
        ("电路基础理论课_学生名单.xlsx", "电路基础理论课_补交表.xlsx"),
        # 上学期那份成绩记分册:名字里还带雨课堂导出的时间戳
        ("数字电路与逻辑设计实验（一）成绩记分册_1774943315001.xlsx",
         "数字电路与逻辑设计实验（一）_补交表.xlsx"),
        # 教务导出的"学生名册"
        ("2026春_电路基础_学生名册.xlsx", "2026春_电路基础_补交表.xlsx"),
        # 认不出来就**不猜课名**,原样接后缀 —— 猜错课名比名字丑严重得多
        ("名单.xlsx", "名单_补交表.xlsx"),
        ("花名册_不存在.xlsx", "花名册_不存在_补交表.xlsx"),
    ],
    ids=["雨课堂学生名单", "成绩记分册带时间戳", "教务学生名册", "认不出_名单", "认不出_花名册"],
)
def test_table_name_is_derived_from_roster_name(roster: str, expect: str):
    got = config.submission_table_for(Path("/somewhere/花名册") / roster)
    assert got.name == expect


def test_table_sits_next_to_the_roster():
    """同目录是硬要求:补交表和名单分开两个目录,等于又多一个"忘了改"的地方。"""
    roster = Path("/data/花名册/电路基础理论课_学生名单.xlsx")
    assert config.submission_table_for(roster).parent == roster.parent


def test_derivation_does_not_mutate_the_roster_path():
    """别顺手改到传进来的对象上 —— 调用方还要用它去读名单。"""
    roster = Path("/data/花名册/电路基础理论课_学生名单.xlsx")
    before = str(roster)
    config.submission_table_for(roster)
    assert str(roster) == before


# ─── 接线:配置里的那个常量,必须真的是推导出来的 ──────────────────────

def test_default_table_path_is_derived_from_default_roster(isolated_logs, tmp_path):
    """**这条才是原 bug 的回归测试。**

    在**子进程**里跑,是因为本进程的 `config` 已经被 autouse 的
    `isolated_submission_table` 指到临时路径上了 —— 那里两个值都是手写的,
    再断言"推导关系"就成了自己证自己。子进程里清掉 `ROSTER_PATH` /
    `SUBMISSION_TABLE` 两个环境变量,拿到的才是配置的**默认值**,
    也就是线上真正会用的那两个。

    断言的是关系而不是具体字符串:换成什么课名都对,除非有人把"跟着花名册走"
    这件事改回写死文件名。
    """
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("ROSTER_PATH", "SUBMISSION_TABLE")}
    env["TA_LOGS_DIR"] = isolated_logs          # 子进程也别碰生产日志
    env["PYTHONIOENCODING"] = "utf-8"
    code = (
        "import json; from src import config as c;"
        "t = c.SUBMISSION_TABLE_PATH; r = c.GRADEBOOK_PATH;"
        "print(json.dumps({'roster': str(r), 'table': str(t),"
        " 'derived': str(c.submission_table_for(r))}))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"子进程没跑通:stderr={out.stderr!r}"
    got = json.loads(out.stdout.strip().splitlines()[-1])

    assert got["table"] == got["derived"], (
        f"补交表路径不是从花名册推导出来的:\n"
        f"  花名册 = {got['roster']}\n"
        f"  实际   = {got['table']}\n"
        f"  应当   = {got['derived']}"
    )
    assert Path(got["table"]).parent == Path(got["roster"]).parent


# ─── 写不进去的时候,不许说"已登记" ─────────────────────────────────

def test_refuses_to_create_a_table_in_a_missing_directory(tmp_path: Path):
    """父目录不在 = 路径是错的,这时**不许 mkdir**。

    自动 mkdir 的后果正是上一个 bug 的形状:在一个没人会去看的地方悄悄建表、
    悄悄记下补交。所以宁可不写。
    """
    from src.utils.excel_ops import ensure_submission_table

    target = tmp_path / "不存在的目录" / "补交表.xlsx"
    with pytest.raises(FileNotFoundError):
        ensure_submission_table(target)
    assert not target.exists()
    assert not target.parent.exists(), "拒绝了却还是把目录建出来了"


def test_write_failure_replies_that_it_did_not_register(monkeypatch, tmp_path, isolated_logs):
    """写盘失败 → 回执必须明说"没登记上",而且不能出现「已登记」。"""
    monkeypatch.setattr(
        config, "SUBMISSION_TABLE_PATH",
        tmp_path / "不存在的目录" / "补交表.xlsx", raising=True,
    )
    result = submission.handle(POLLUTING_MESSAGE)

    assert result["type"] == "submission"
    assert result["reply"].startswith("❌")
    assert "已登记" not in result["reply"], "没写进去却回了「已登记」—— 这正是要堵的那件事"
    assert result.get("error"), "失败要带上原因,不然排不了障"
    assert not (tmp_path / "不存在的目录").exists()

    # 失败也要留痕:日志里能查到这条,否则助教永远不知道有学生补交没记上
    logs = list(Path(isolated_logs).glob("*.log"))
    assert logs, "失败事件没进日志"
    text = "\n".join(p.read_text(encoding="utf-8") for p in logs)
    assert "submission_write_error" in text
    # 日志里不该出现学号:原始消息已经被 process_input 记过一份了
    assert "20230001" not in text


def test_success_reply_means_the_row_is_really_in_the_table(isolated_submission_table):
    """反方向也钉一下:回了「✅ 已登记」,那表里就**必须**真有这一行。

    (少了这条,把 handle 改成永远回"❌ 没登记上"也能让上面那条通过。)
    """
    result = submission.handle(POLLUTING_MESSAGE)
    assert "✅ 已登记" in result["reply"]

    from src.utils.excel_ops import load_submission_history
    table = Path(isolated_submission_table["table"])
    rows = load_submission_history(table)
    assert len(rows) == 1
    assert rows[0]["学号"] == "20230001"
