"""集中配置:所有路径、API Key、阈值都在这里改,一处生效。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── 项目根目录 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ─── Windows 侧数据目录(WSL 下的 /mnt/c/... 访问形式) ──────────
# 默认用 ~/ykt_questions (跨平台);在 .env 里覆盖 WINDOWS_ROOT 可改
_DEFAULT_ROOT = Path(os.path.expanduser("~/ykt_questions"))
WINDOWS_ROOT = Path(os.getenv("WINDOWS_ROOT", str(_DEFAULT_ROOT)))

GRADEBOOK_PATH = (
    WINDOWS_ROOT
    / "数电资料"
    / "数字电路与逻辑设计实验（一）成绩记分册_1774943315001.xlsx"
)
SUBMISSION_TABLE_PATH = (
    WINDOWS_ROOT / "数字电路与逻辑设计实验（一）补交表.xlsx"
)
FAQ_PATH = WINDOWS_ROOT / "常问问题.txt"
MATERIALS_DIR = WINDOWS_ROOT / "materials"

# ─── 项目内目录 ──────────────────────────────────────────────
INDEX_DIR = PROJECT_ROOT / "data" / "index"
LOGS_DIR = PROJECT_ROOT / "data" / "logs"
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"

INDEX_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

BM25_INDEX_PATH = INDEX_DIR / "bm25_index.pkl"

# ─── LLM(MinMax) ────────────────────────────────────────────
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
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
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

# ─── 花名册解析参数(参考成绩记分册 xlsx 实际格式) ─────────────
ROSTER_HEADER_ROW = 5       # 表头行号(1-based)
ROSTER_DATA_START_ROW = 7   # 第一条学生数据行号
ROSTER_COL_ID = 2           # 学号列(B)
ROSTER_COL_NAME = 3         # 姓名列(C)

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

# ─── 日志 ────────────────────────────────────────────────────
def log_path_for_month() -> Path:
    from datetime import datetime
    return LOGS_DIR / f"{datetime.now().strftime('%Y-%m')}.log"


def llm_log_path() -> Path:
    return LOGS_DIR / "llm.log"
