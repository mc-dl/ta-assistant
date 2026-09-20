"""pytest 配置:让 `pytest` 能找到 src 包,并把**整个测试套件和网络隔开**。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402 —— 必须排在 sys.path.insert 之后

# 桩答复。挑一个**一眼能认出不是真答案**的串,免得将来有人把桩的输出
# 误当成大模型的真实回答来排查问题。
CANNED_REPLY = "(单测桩:未调用真实大模型)"


def _no_network(*args, **kwargs):
    raise AssertionError(
        "单测里出现了真实网络请求。要么把数据打桩,要么把这段逻辑挪到 "
        "scripts/ 下的评测脚本里(那些脚本才允许联网)。"
    )


@pytest.fixture(autouse=True)
def offline_http(monkeypatch):
    """自动生效:把 HTTP 出口换成假的,单测期间绝不联网。

    ── 为什么要加这个(2026-09-20 实测)────────────────────────────
    单测原来**真的在调大模型**:只跑 `test/test_main.py` 一次,
    `data/logs/llm.log` 就多了 4 行、耗时 16.13 秒(其中 15.8 秒是网络等待)。
    代价有三个,而且都是隐性的:
      · 断网/没配 key 时看着像"测试挂了",其实是被测代码自己要走网络;
      · 每跑一次烧一次 API 额度,还会被限流 → 偶发失败(flaky);
      · 断言其实只检查 `type`,却把大模型拉成了必经路径,白等十几秒。
    所以这里在**低层**打桩:只换掉 `requests.post`,让 `MinMaxClient.chat`
    本身保持真实 —— 这样传输层(请求头、状态码处理、响应解析)仍然可测
    (见 test_llm_client.py),而任何一处漏网的真实请求都会当场炸出来。

    返回 `calls`:一个列表,每项是 `{"url", "json", "headers"}`,
    测试可以拿它断言"发了什么请求"。不用也可以忽略。
    """
    import requests
    from src.llm import minmax as minmax_mod

    calls: list[dict] = []

    class _FakeResponse:
        def __init__(self, status: int, payload: dict):
            self.status_code = status
            self._payload = payload
            self.text = "" if status == 200 else f"(假响应 HTTP {status})"

        def json(self) -> dict:
            return self._payload

    def _fake_post(url, json=None, headers=None, timeout=None, **kw):  # noqa: A002
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(200, {
            "choices": [{"message": {"content": CANNED_REPLY}}],
        })

    monkeypatch.setattr(requests, "post", _fake_post, raising=True)
    # 其他出口一律焊死:万一以后有人用 get / Session 发请求,当场报错而不是静默联网
    monkeypatch.setattr(requests, "get", _no_network, raising=True)
    monkeypatch.setattr(requests, "request", _no_network, raising=True)
    monkeypatch.setattr(requests.sessions.Session, "request", _no_network, raising=True)
    # 别往生产的 data/logs/llm.log 里写桩调用记录 —— 那份日志是用来数
    # "线上真实调了多少次"的,被单测污染就数不准了
    monkeypatch.setattr(minmax_mod, "_log_llm", lambda *a, **k: None, raising=True)
    return calls


@pytest.fixture(autouse=True)
def isolated_logs(monkeypatch, tmp_path):
    """自动生效:把日志目录指到临时路径,单测**绝不写进 `data/logs/`**(生产日志)。

    ── 为什么要加(2026-09-20 实测,而且是写挖矿脚本时才发现的)──────────
    写 `scripts/mine_faq_candidates.py` 时要拿真实日志验证"到底挖出了哪些问法",
    挖出来一看,高频缺口前三名是:

        「随便问问」46 次、「1.36 怎么做」14 次、「补交怎么办?」13 次

    —— 全是 `test/` 里的字面量。机械核对:**543 条 `qa_kind` 问句里有 521 条
    (95.9%)** 的原文恰好等于 `test/` 里的字符串字面量。也就是说那份"生产日志"里
    96% 是我们自己跑单测写进去的,挖矿挖的是自己的测试用例。

    **危害不止"挖错了东西":**
      · 单测每跑一次就往生产日志灌几十行假问句,而这份日志是**唯一**能回答
        "学生到底在问什么"的数据源,污染之后无法回溯剔除;
      · 于是所有基于它的判断(该补哪条 FAQ、FAQ 命中率、事务题占比)全部失真,
        而我先前已经拿它算过"事务题 90.3% 命中 FAQ"这种数 —— 那些数现在也可疑;
      · 它还会**一直涨**:同一份日志在几次全量测试之间从 451 → 474 → 543 条,
        数字每次都不一样,拿它做的前后对比全都不可比。

    同类问题这个项目已经踩过一次 —— 见 `offline_http` 里对 `_log_llm` 的打桩
    ("那份日志是用来数线上真实调了多少次的,被单测污染就数不准了")。
    当时只堵了 `llm.log`,没堵主日志,这次一起堵上。

    想测日志本身的,自己指定路径,不要依赖 `config.LOGS_DIR`。

    返回被指过去的目录(字符串),给 `offline_env` 往子进程里传 ——
    monkeypatch 跨不过 `subprocess`,子进程只认环境变量。
    """
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs", raising=True)
    return str(tmp_path / "logs")


@pytest.fixture(autouse=True)
def isolated_course_facts(monkeypatch, tmp_path):
    """自动生效:把「课程事务事实表」指到一个不存在的临时路径。

    理由和 `offline_http` 是同一个 —— **单测不该依赖助教本机那份数据**。
    `qa.handle()` 在调用方没传 `facts=` 时会去读 `config.COURSE_FACTS_PATH`
    (默认 `~/ykt_questions/课程事务.txt`)。若哪天助教真建了那个文件,
    `test_qa_flow.py` 里断言 `sources == ["FAQ(常问问题.txt)"]` 的那些用例
    就会莫名其妙变红,而它们跟事实表一点关系都没有 —— 这种"看别人机器脸色"
    的红最难查(2026-09-20 踩过一次同类:基线只存在于一侧,见 test_eval_baseline.py)。

    想测事实表的,自己传 `facts=[...]` 或把路径 monkeypatch 到临时文件上去。
    """
    monkeypatch.setattr(
        config, "COURSE_FACTS_PATH", tmp_path / "课程事务_不存在.txt", raising=True
    )


@pytest.fixture(autouse=True)
def isolated_faq(monkeypatch, tmp_path):
    """自动生效:把 FAQ 路径指到一个**不存在**的临时文件。

    这是三个隔离 fixture 里**代价最高**的一个:`FAQ_PATH` 默认指向
    `~/ykt_questions/常问问题.txt`,那是 37 条**手写**材料的唯一副本,
    没有版本库兜底。而 `【记录】` 入口(见 src/handlers/record.py)会往它**追加写**。

    也就是说:只要有一个测试顺手调一句 `process("【记录】问题:…")`,
    它就真的往助教的生产 FAQ 里写了一条 —— 单测修改生产数据,而且改的是
    没有备份的那份。和 `data/logs` 那次(见 `isolated_logs`)是同一类事故,
    但这次连"事后把测试写的那几行挑出来删掉"都做不到干净:
    FAQ 是按条目手写的,没有时间戳可以区分谁写的。

    指向**不存在**的路径(而不是空文件)是刻意的:这样"记录时 FAQ 文件不存在"
    这条分支在默认情况下就会走到,想测写入的用例必须自己先造一份 ——
    测试对"文件从哪儿来"这件事保持诚实,不会因为 fixture 悄悄塞了个空文件
    而掩盖掉"路径配错了"这种真实故障。

    想测 QA 检索的,自己造文件并 monkeypatch `config.FAQ_PATH`。
    """
    monkeypatch.setattr(
        config, "FAQ_PATH", tmp_path / "常问问题_不存在.txt", raising=True
    )
    return tmp_path / "常问问题_不存在.txt"


@pytest.fixture(autouse=True)
def isolated_submission_table(monkeypatch, tmp_path) -> dict[str, str]:
    """自动生效:花名册与补交表都指到临时目录。

    ── 为什么要加(2026-09-20 发现,比日志那次严重)──────────────────
    补交表 `~/ykt_questions/数字电路与逻辑设计实验（一）补交表.xlsx` 里有 **398 行**,
    而它只包含 **1 个学号、1 个姓名、4 种原文**,四种原文**逐字出现在 `test/` 里**:

        ×101  「张三 20230001 昨天数电作业没交」这类
        × 99  「学生A同学通过雨课堂补交了第一次作业…」
        …

    也就是说那张"补交记录表"**没有一条是学生发的**,全部是 `test_main.py` /
    `test_bridge_stdout_clean.py` 每跑一次就往里追加几行攒出来的。时间戳也对得上:
    集中在 2026-09-20 这一天(我反复跑全量测试的那天)和 09-17。

    和日志污染(`isolated_logs`)是同一类事故,但**性质更差**:
      · 日志只是"读起来失真",补交表是**要交给助教登记的正式记录**,
        混着 398 行假数据,真的补交查起来根本认不出来;
      · 它还会**盖住真故障**:整张表 398 行的校验状态都是「名单缺失」,
        与"花名册从来没读出来过"这个 bug 长得一模一样,于是那个 bug
        被这堆噪声掩护了很久(见 knowledge_base.md §8 第 12、13 条)。

    和 `isolated_faq` 一样,这里把花名册指到一个**不存在**的路径:
    "名单读不出来"因此是单测里的默认分支,想测校验通过的用例必须自己造名单 ——
    测试不该看助教本机那份名单的脸色(同 `isolated_course_facts` 的理由)。

    返回两个被指过去的路径(字符串),给 `offline_env` 往子进程里传。
    """
    paths = {
        "roster": str(tmp_path / "花名册_不存在.xlsx"),
        "table": str(tmp_path / "补交表.xlsx"),
    }
    monkeypatch.setattr(config, "GRADEBOOK_PATH", Path(paths["roster"]), raising=True)
    monkeypatch.setattr(config, "SUBMISSION_TABLE_PATH", Path(paths["table"]), raising=True)
    return paths


@pytest.fixture
def offline_env(isolated_logs, isolated_submission_table) -> dict:
    """给 **subprocess** 类测试用的环境:清空两个 API Key,子进程也不会联网。

    用途:`test_bridge_stdout_clean.py` 是 `subprocess.run` 起 bridge,
    fixture 的 monkeypatch 管不到子进程,只能从环境变量入手。
    **`SUBMISSION_TABLE` / `ROSTER_PATH` 同理** —— 不传的话,
    `test_bridge_no_extra_newlines` 那条(原文是"王小明 20230001 漏交第1次作业")
    会照直往助教的生产补交表里追加一行。
    `config.load_dotenv()` 用默认的 `override=False`,**已存在的环境变量不会被
    .env 覆盖**,所以把 key 显式设成空串就能让 `MinMaxClient.available()` 为假、
    `chat()` 直接返回空串(见 src/llm/minmax.py 开头两行)。

    **`TA_LOGS_DIR` 也是同一个道理**,而且更容易被忽略:子进程里的
    `config.LOGS_DIR` 是它自己 import 时算出来的,`isolated_logs` 改的是父进程的
    那个对象,改不到它。不传这个变量的话,bridge 子进程会照直往
    `data/logs/2026-09.log` 写 —— 实测跑一轮单测就多 8 行,而且 `qa_kind`
    也在里面,正好是挖矿脚本要吃的那条事件。
    """
    env = dict(os.environ)
    env["LLM_API_KEY"] = ""
    env["MINMAX_API_KEY"] = ""
    env["TA_LOGS_DIR"] = isolated_logs
    env["SUBMISSION_TABLE"] = isolated_submission_table["table"]
    env["ROSTER_PATH"] = isolated_submission_table["roster"]
    return env
