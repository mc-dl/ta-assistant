# 系统设计文档

> 对应需求文档 `requirements.md`,描述**怎么实现**。

## 1. 总体架构

```
┌───────────┐  微信转发   ┌──────────────┐
│ 助教(我) │ ─────────▶ │   OpenClaw   │
└───────────┘           │ (WSL Ubuntu) │
      ▲                 └──────┬───────┘
      │ 微信回复                │ 子进程调用 / HTTP
      │                        ▼
      │                 ┌──────────────────┐
      └─────────────────┤   ta-assistant   │
                        │   (本项目)        │
                        └──┬──────────────┬┘
                           │              │
           ┌───────────────┘              └──────────────┐
           ▼                                             ▼
   ┌───────────────┐                           ┌────────────────┐
   │  消息分类器    │                           │  MinMax LLM   │
   │ (Classifier) │                           │    API        │
   └───┬───────┬───┘                           └────────────────┘
       │       │                                       ▲
       ▼       ▼                                       │
  补交 Handler  答疑 Handler ──── FAQ / RAG 检索 ──────┘
       │       │
       ▼       ▼
   Excel 读写  知识库索引 (BM25)
       │       │
       ▼       ▼
   补交表     数电资料目录
```

## 2. 调用时序

### 2.1 补交场景

```
助教:转发"张三 20230001 昨天数电作业没交"
  │
  └──▶ OpenClaw 调 openclaw_bridge.py --message "..."
         │
         └──▶ main.process(msg)
                ├─ classifier.classify(msg) → "submission"
                ├─ submission.handle(msg)
                │    ├─ 正则抽 学号=20230001, 姓名=张三
                │    ├─ 读花名册 → 校验学号 存在 ✓
                │    ├─ 写补交表(追加一行)
                │    └─ 写日志
                └─ 返回 {type:"submission", reply:"✅ 已登记:张三(20230001)第 X 次补交"}
```

### 2.2 答疑场景

```
助教:转发"proteus 在哪下载?"
  │
  └──▶ main.process(msg)
         ├─ classifier.classify(msg) → "question"
         ├─ qa.handle(msg)
         │    ├─ FAQ 检索 → 命中"Proteus 下载"那条,score=7.2
         │    ├─ 拼 prompt: {context: FAQ 原文, question: ...}
         │    ├─ 调 MinMax API → 返回润色后的答复
         │    └─ 写日志
         └─ 返回 {type:"question", reply:"Proteus 需要自己找激活版..."}
```

### 2.3 RAG 场景

```
助教:转发"什么是竞争冒险?"
  │
  └──▶ qa.handle(msg)
        ├─ FAQ 检索 → 最高分 1.2,未命中
        ├─ RAG 检索(BM25)→ 命中"面试--数电.docx" 第 4 题片段
        ├─ 拼 prompt: {context: 片段文本, question, 要求: 基于资料作答}
        ├─ 调 MinMax API
        └─ 返回答复(末尾附"参考自:面试--数电.docx")
```

## 3. 模块设计

### 3.1 `src/config.py`
集中所有配置,支持 `.env` 覆盖。

```python
# 伪代码要点
WINDOWS_ROOT = Path("/mnt/c/Users/YOUR_USERNAME/Downloads/雨课堂问题")
GRADEBOOK_PATH = WINDOWS_ROOT / "数电资料" / "数字电路与逻辑设计实验（一）成绩记分册_1774943315001.xlsx"
SUBMISSION_TABLE_PATH = WINDOWS_ROOT / "数字电路与逻辑设计实验（一）补交表.xlsx"
FAQ_PATH = WINDOWS_ROOT / "常问问题.txt"
MATERIALS_DIR = WINDOWS_ROOT / "数电资料"

PROJECT_ROOT = Path(__file__).parent.parent
INDEX_DIR = PROJECT_ROOT / "data" / "index"
LOGS_DIR = PROJECT_ROOT / "data" / "logs"

MINMAX_API_KEY = os.getenv("MINMAX_API_KEY", "")
MINMAX_GROUP_ID = os.getenv("MINMAX_GROUP_ID", "")
MINMAX_MODEL = os.getenv("MINMAX_MODEL", "abab6.5s-chat")

FAQ_HIT_THRESHOLD = 4.0
RAG_TOP_K = 5
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
```

### 3.2 `src/handlers/classifier.py`

```
def classify(msg: str) -> Literal["submission", "question", "unknown"]:
    # 1) 规则:含"补交/漏交/没交/忘了交" 且 含 8位学号 → submission
    # 2) 规则:含 "?/?/怎么/为什么/在哪/吗/可以" → question
    # 3) 两者都不满足 → LLM 兜底(prompt: 分类为 submission/question/other, 只输出 JSON)
```

### 3.3 `src/handlers/submission.py`

```
def handle(msg: str) -> dict:
    student_id = extract_student_id(msg)      # re.search(r"\b\d{8}\b", msg)
    name = extract_name(msg) or lookup_name_by_id(student_id)
    hw_no = extract_homework_number(msg)      # 可能为 None
    roster = load_gradebook()                 # 从花名册 Excel 读姓名/学号
    status = validate(student_id, name, roster)
    append_submission_row(SUBMISSION_TABLE_PATH, {
        "登记时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "学号": student_id,
        "姓名": name,
        "作业次数": hw_no,
        "原始消息": msg,
        "校验状态": status,
    })
    return {"type": "submission", "reply": format_reply(...)}
```

### 3.4 `src/handlers/qa.py`

```
def handle(msg: str) -> dict:
    # 1) FAQ 先搜
    faq_hits = faq_retriever.search(msg, top_k=3)
    if faq_hits and faq_hits[0].score >= FAQ_HIT_THRESHOLD:
        context = format_faq(faq_hits)
        source = "FAQ"
    else:
        # 2) 知识库 RAG
        rag_hits = rag_retriever.search(msg, top_k=RAG_TOP_K)
        context = format_rag(rag_hits)
        source = [h.source_file for h in rag_hits]
    reply = minmax.chat(system_prompt, user_prompt_with_context)
    return {"type": "question", "reply": reply + f"\n(参考:{source})"}
```

### 3.5 `src/rag/indexer.py`

职责:一次性扫描 `数电资料/` 目录,分块,建 BM25 索引,存盘。

```
def build_index():
    chunks = []   # List[{"text": ..., "source": ..., "section": ...}]
    for f in walk(MATERIALS_DIR):
        text = parse_document(f)
        for c in split_into_chunks(text, CHUNK_SIZE, CHUNK_OVERLAP):
            chunks.append({"text": c, "source": f.name, "section": ...})
    tokenized = [jieba.lcut(c["text"]) for c in chunks]
    bm25 = BM25Okapi(tokenized)
    pickle.dump({"bm25": bm25, "chunks": chunks}, INDEX_DIR / "bm25_index.pkl")
```

### 3.6 `src/rag/retriever.py`

```
class BM25Retriever:
    def __init__(self, index_path): self.load(index_path)
    def search(self, query, top_k=5):
        tokens = jieba.lcut(query)
        scores = self.bm25.get_scores(tokens)
        return top_k_with_metadata(scores, self.chunks, top_k)
```

### 3.7 `src/llm/minmax.py`

包装 MinMax ChatCompletion API(HTTP 调用),带重试、超时、日志。

```
class MinMaxClient:
    def __init__(self, api_key, group_id, model): ...
    def chat(self, system: str, user: str, temperature=0.3) -> str:
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]}
        # POST https://api.minimax.chat/v1/text/chatcompletion_v2
        # 带超时、3 次重试
```

### 3.8 `src/utils/doc_parser.py`

```
def parse_document(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf": return _parse_pdf(path)       # pdfplumber
    elif ext == ".docx": return _parse_docx(path)   # python-docx
    elif ext == ".doc": return _parse_doc(path)     # 先 libreoffice 转 docx
    elif ext == ".xlsx": return _parse_xlsx(path)   # openpyxl,拼 sheet/row/col
    elif ext == ".txt": return path.read_text("utf-8")
    else: return ""
```

### 3.9 `src/utils/excel_ops.py`

```
def ensure_submission_table(path: Path):
    if not path.exists():
        create_with_header(path, ["登记时间","学号","姓名","作业次数","原始消息","校验状态"])

def append_row(path: Path, row_dict: dict):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    ws.append([row_dict.get(k, "") for k in HEADERS])
    wb.save(path)

def load_roster() -> dict[str, str]:
    # 返回 {学号: 姓名}
```

### 3.10 `src/main.py`

提供两种入口:

- **CLI**:`python -m src.main --message "..."` → 打印 JSON
- **HTTP**(可选):`uvicorn src.main:app --port 8765` → OpenClaw POST `/process`

## 4. 数据流示例

### 输入
```
学生A同学通过雨课堂补交了第一次作业,学号20230001,姓名张三
```

### 分类器输出
```json
{"type": "submission"}
```

### 补交 Handler 抽取
```python
{"student_id": "20230001", "name": "张三", "hw_no": "第 1 次", 
 "status": "OK", "raw": "...",  "time": "2026-04-19 14:32"}
```

### 补交表新增一行
| 登记时间 | 学号 | 姓名 | 作业次数 | 原始消息 | 校验状态 |
|---|---|---|---|---|---|
| 2026-04-19 14:32 | 20230001 | 张三 | 第 1 次 | 学生A... | OK |

### 返回
```json
{"type":"submission","reply":"✅ 已登记:张三(20230001)第 1 次补交,校验状态 OK"}
```

## 5. 错误处理策略

| 错误 | 处理 |
|---|---|
| 花名册文件不存在 | 告警日志 + 不阻断,学号校验跳过,"校验状态"标记"名单缺失" |
| 补交表写入失败 | 重试 3 次,仍失败 → 返回错误消息,记日志 |
| MinMax API 超时 | 退化为"把 FAQ/RAG 原文直接回复",避免完全不可用 |
| 索引文件不存在 | 提示跑 `scripts/build_index.py`,该次请求退化为"抱歉,知识库未就绪" |
| 消息分类失败 | 默认当 `question` 处理 |

## 6. 目录与路径约定

- 所有 Windows 路径在 WSL 里用 `/mnt/c/Users/YOUR_USERNAME/Downloads/...`
- 所有项目内路径用 `PROJECT_ROOT = Path(__file__).resolve().parent.parent` 派生
- 日志文件按月分片:`data/logs/2026-04.log`

## 7. 依赖清单(`requirements.txt`)

```
openpyxl>=3.1
pdfplumber>=0.10
python-docx>=1.1
rank-bm25>=0.2.2
jieba>=0.42
requests>=2.31
python-dotenv>=1.0
fastapi>=0.110       # 可选,HTTP 模式
uvicorn>=0.29        # 可选
pytest>=8.0          # 测试
```

## 8. 测试策略

- **单元测试**(`tests/test_*.py`):
  - `test_classifier.py`:给 10 条样本(补交 5 / 提问 5),验证分类正确
  - `test_submission_extract.py`:学号、姓名、作业次数抽取正确
  - `test_doc_parser.py`:每种格式能正确提取文本
  - `test_faq_retriever.py`:FAQ 命中常见 Q 时 score 高
- **集成测试**(`scripts/demo.py`):端到端跑 3 个样例消息
- **人肉验收**:按 `requirements.md` §6 的清单过一遍

## 9. 上线 checklist

- [ ] WSL 里能运行 `python -m src.main --message "test"` 不报错
- [ ] 花名册、补交表、FAQ 三个 Windows 路径都能读到
- [ ] `scripts/build_index.py` 成功生成 `data/index/bm25_index.pkl`
- [ ] MinMax API 在 `.env` 配好,调用 `minmax.chat("你好","你是谁")` 返回内容
- [ ] OpenClaw 配置好调用脚本,端到端跑通一次补交、一次 FAQ、一次 RAG
