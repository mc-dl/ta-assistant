"""集中配置:所有路径、API Key、阈值都在这里改,一处生效。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

# ─── 项目根目录 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ─── Windows 侧数据目录(WSL 下的 /mnt/c/... 访问形式) ──────────
# 默认用 ~/ykt_questions (跨平台);在 .env 里覆盖 WINDOWS_ROOT 可改
_DEFAULT_ROOT = Path(os.path.expanduser("~/ykt_questions"))
WINDOWS_ROOT = Path(os.getenv("WINDOWS_ROOT", str(_DEFAULT_ROOT)))

# ─── 花名册 ──────────────────────────────────────────────────
# 【2026-09-20 修】原先指向 `WINDOWS_ROOT/"数电资料"/成绩记分册_….xlsx`,
# 而**那个目录根本不存在** —— 于是 load_roster() 一直返回 {},
# "学号/姓名校验"从未生效过。修之前所有补交记录的校验状态都是「名单缺失」。
#
# 现在单独一个 `花名册/` 目录,理由有两条,都不只是"好看":
#   ① 名单**不是教学资料**。旧的那份记分册原先躺在 `materials/` 里,
#      被 build_index.py 当成课程资料解析进了 RAG 索引(见 knowledge_base.md §8 第 12 条);
#   ② 换学期 = 换文件。名字固定,助教把新导出的名单覆盖成这个文件名即可,
#      不用改代码。当前这份来自雨课堂导出(电路基础理论课,169 人)。
ROSTER_DIR = WINDOWS_ROOT / "花名册"
GRADEBOOK_PATH = Path(
    os.getenv("ROSTER_PATH", str(ROSTER_DIR / "电路基础理论课_学生名单.xlsx"))
)
# 补交表:**由花名册路径推导**,不再写死文件名(同目录、同课名、后缀换「补交表」)。
#
# 【2026-09-20 改】原来是 `WINDOWS_ROOT / "数字电路与逻辑设计实验（一）补交表.xlsx"`
# 这种写死的名字,这学期换成「电路基础」之后,它指着一条**根本不存在的路径**。
# 后果不是报错,是**静默**:下一个补交的学生会让 ensure_submission_table 新建一张
# 空表、机器人照样回「✅ 已登记」,而助教那份真表一直是空的(见 knowledge_base.md)。
#
# 为什么正确的做法是"跟着花名册走":补交是**按课**归属的 —— 数电的补交记在数电那份、
# 电路基础的记在电路基础那份 —— 而"现在是哪门课"唯一的权威来源就是 `GRADEBOOK_PATH`
# 指向的那份名单。于是换课只需要换名单一处(覆盖文件或改 `ROSTER_PATH`),
# 补交表自动跟着换,不需要有人记得去改第二个地方。
_ROSTER_NAME_TAILS = ("学生名单", "成绩记分册", "学生名册", "记分册", "名单")
# 分隔符**不能写死下划线**:两份真实文件就不是一个版式 ——
#   `电路基础理论课_学生名单.xlsx`                (下划线 + 名单)
#   `数字电路与逻辑设计实验（一）成绩记分册_1774943315001.xlsx`  (课名直接顶到"成绩记分册")
# 第二种是雨课堂导出的原名,课名和"成绩记分册"之间**没有分隔符**。写死 `_成绩记分册`
# 就漏掉它,推出来的名字里会留住"成绩记分册"五个字(测试里当场抓到了)。
_ROSTER_TAIL_RE = re.compile(r"[-_—\s]*(" + "|".join(_ROSTER_NAME_TAILS) + r")$")


def submission_table_for(roster_path: Path) -> Path:
    """由花名册路径推出这门课的补交表路径:同目录,课名相同,后缀换成「补交表」。

        .../花名册/电路基础理论课_学生名单.xlsx          → .../花名册/电路基础理论课_补交表.xlsx
        .../花名册/数字电路…成绩记分册_1774943315001.xlsx → .../花名册/数字电路…_补交表.xlsx

    认不出的名字**不猜课名**,直接接一个后缀(`名单.xlsx` → `名单_补交表.xlsx`)。
    宁可名字丑一点:猜错课名等于把补交记到别的课上去,而那是要交给老师的东西。
    """
    stem = re.sub(r"[_\-]\d{10,}$", "", roster_path.stem)   # 抹掉雨课堂导出的时间戳尾巴
    m = _ROSTER_TAIL_RE.search(stem)
    if m and m.start() > 0:      # `m.start() == 0` 意味着整个名字就是"名单"两字,
        stem = stem[: m.start()]  # 剥完没有课名了 —— 那还不如保留原名
    return roster_path.with_name(f"{stem}_补交表.xlsx")


# `SUBMISSION_TABLE` 环境变量仍可覆盖 —— 这个口子主要是给**单测**留的:
# `subprocess` 起的 bridge 是另一份进程,`monkeypatch` 跨不过去,只能靠环境变量
# (同 `TA_LOGS_DIR` 的理由)。它会**追加写**,所以单测碰它一次就污染一次。
SUBMISSION_TABLE_PATH = Path(
    os.getenv("SUBMISSION_TABLE", str(submission_table_for(GRADEBOOK_PATH)))
)
FAQ_PATH = WINDOWS_ROOT / "常问问题.txt"
MATERIALS_DIR = WINDOWS_ROOT / "materials"
# 课程事务事实表(带生效期的"现行口径",格式与理由见 src/utils/course_facts.py)。
# 它是**人维护**的:助教改了截止时间/提交方式就改这里一处。
# 文件不存在 = 这个功能静默休眠,答疑行为与加它之前完全一致。
COURSE_FACTS_PATH = WINDOWS_ROOT / "课程事务.txt"

# ─── 项目内目录 ──────────────────────────────────────────────
INDEX_DIR = PROJECT_ROOT / "data" / "index"
# 日志目录可以用 TA_LOGS_DIR 覆盖。**这不是为了部署,是为了单测**:
# `data/logs/YYYY-MM.log` 是唯一能回答"学生到底在问什么"的数据源,
# 而 `subprocess` 起的子进程(见 test_bridge_stdout_clean.py)不受 pytest 的
# monkeypatch 管,只能靠环境变量把日志指走。为什么非要堵:
# 2026-09-20 实测那份日志里 543 条 qa_kind 有 521 条(95.9%)是单测写进去的。
LOGS_DIR = Path(os.getenv("TA_LOGS_DIR", str(PROJECT_ROOT / "data" / "logs")))
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"

INDEX_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

BM25_INDEX_PATH = INDEX_DIR / "bm25_index.pkl"

# ─── LLM(大模型通道) ─────────────────────────────────────────
# 为什么要单独有 LLM_* 这一层:业务代码(答疑 / 分类)只关心
# "给一段 prompt、拿回一段文本",不该关心是哪家 API。
# 各家差异(URL、鉴权头、响应结构)全部收在 src/llm/ 里,
# 于是换供应商 = 改 .env,不用动任何业务代码。
#
# 当前默认通道:OpenCode Go 套餐(OpenAI 兼容接口)。
# 旧的 MiniMax 变量保留,只在 LLM_API_KEY 没填时兜底(见 src/llm/minmax.py)。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "opencode-go")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4.1-flash")
LLM_ENDPOINT = os.getenv(
    "LLM_ENDPOINT",
    "https://opencode.ai/zen/go/v1/chat/completions",
)
# OpenCode Go 强制要求这个请求头:缺了会返回 400 MissingSessionID
# (2026-09-17 实测:不带头 400,带上头 200。值只要是个稳定的唯一串即可)
LLM_SESSION_HEADER = os.getenv("LLM_SESSION_HEADER", "x-opencode-session")
# 有些网关(Cloudflare)会拦掉 python-urllib/requests 的默认 UA,伪装成浏览器更稳
LLM_USER_AGENT = os.getenv(
    "LLM_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
)

# ─── LLM 旧通道:MinMax(保留作兜底/回滚) ──────────────────────
MINMAX_API_KEY = os.getenv("MINMAX_API_KEY", "")
MINMAX_GROUP_ID = os.getenv("MINMAX_GROUP_ID", "")
MINMAX_MODEL = os.getenv("MINMAX_MODEL", "abab6.5s-chat")
MINMAX_ENDPOINT = os.getenv(
    "MINMAX_ENDPOINT",
    "https://api.minimax.chat/v1/text/chatcompletion_v2",
)
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "20"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# ─── 检索与分类参数 ───────────────────────────────────────────
FAQ_HIT_THRESHOLD = float(os.getenv("FAQ_HIT_THRESHOLD", "4.0"))
# 2026-09-20 由 5 提到 7:一份电路基础作业语料的中位分块数是 5、最长 11,
# 只取 5 块会把同一条解答的后面几块切掉 —— 实测问 10.46 时模型只拿到前两问的推导,
# 如实回答"第三项资料未给详细步骤"。取 7 能覆盖 93% 的题目文件(68/73)。
# 索引里的锚点已复制进每块(见 src/rag/indexer.py),所以这 7 块基本都落在同一道题上。
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "7"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "400"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# ─── 补交表字段 ──────────────────────────────────────────────
SUBMISSION_HEADERS = [
    "登记时间",
    "学号",
    "姓名",
    "作业次数",
    "原始消息",
    "校验状态",
]

# ─── 花名册解析参数 ───────────────────────────────────────────
# 【2026-09-20 改】原先写死了"表头在第 5 行、数据从第 7 行、学号在 B 列、姓名在 C 列",
# 那是**只对成绩记分册一种版式**成立的坐标。而花名册是会换的(每学期一份、
# 雨课堂导出、教务导出…),版式一变,写死的坐标会**安静地读成空名单** ——
# 这正是这个 bug 藏了好几个月没人发现的原因。
# 改成按**表头文字**找:扫前 N 行,哪一行同时出现学号类表头和姓名类表头,
# 就认那一行是表头,数据从它下面一行开始,列取表头所在列。
ROSTER_HEADER_SCAN_ROWS = 12
ROSTER_ID_HEADERS = ("学号", "学生学号", "学号/工号", "工号")
ROSTER_NAME_HEADERS = ("姓名", "学生姓名")

# ─── 消息前缀过滤 ─────────────────────────────────────────────
# 只有带此前缀的消息才视为学生转发,否则跳过处理
# STUDENT_FORWARD_PREFIXES:支持多个前缀变体,任一匹配即触发路由
_STUDENT_FORWARD_PREFIXES_DEFAULT = [
    "【转发学生提问】",
    "【转发同学提问】",   # 用户可能打"同学"而不是"学生"
    "【转发学生消息】",
    "[转发学生提问]",     # 半角兼容
]
# 从 .env 读取自定义前缀列表(逗号分隔),未配置则用默认值
_env_prefixes = os.getenv("FORWARD_PREFIX", "")
if _env_prefixes:
    STUDENT_FORWARD_PREFIXES = [p.strip() for p in _env_prefixes.split(",") if p.strip()]
else:
    STUDENT_FORWARD_PREFIXES = _STUDENT_FORWARD_PREFIXES_DEFAULT
# 向后兼容:STUDENT_FORWARD_PREFIX 指向列表第一个
STUDENT_FORWARD_PREFIX = STUDENT_FORWARD_PREFIXES[0]

# ─── 助教元命令:【记录】 ───────────────────────────────────────
# 助教(不是学生)**直接**发给机器人的指令:把一条"问 + 答"写进 FAQ。
#
# 它刻意**不放进** STUDENT_FORWARD_PREFIXES:那个列表的语义是"这条是学生说的话,
# 去分派给答疑/补交",而【记录】是助教对机器人下的命令,两者通路完全不同;
# 而且两者的闸门顺序**不能反** —— 助教发【记录】时不带转发前缀,
# 放到学生闸门后面会被当成"非学生消息"直接丢掉(加这个入口之前就是这行为)。
# 见 src/main.py 里两处闸门的先后,和 src/handlers/record.py。
RECORD_PREFIX = os.getenv("RECORD_PREFIX", "【记录】")

# ─── 日志 ────────────────────────────────────────────────────
def log_path_for_month() -> Path:
    from datetime import datetime
    return LOGS_DIR / f"{datetime.now().strftime('%Y-%m')}.log"


def llm_log_path() -> Path:
    return LOGS_DIR / "llm.log"
