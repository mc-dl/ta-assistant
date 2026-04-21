# 技术栈说明 — 面试 / 简历素材

> 这份文档专门给面试官和 HR 看,帮助你讲清楚这个项目的技术深度和工程sense。

---

## A. 系统架构与模块划分

**分层清晰,单向依赖:**

```
handlers/  (业务层:分类/补交/答疑)
    ↓
rag/        (检索层:索引/召回)
    ↓
llm/       (外调:MinMax API)
    ↓
utils/     (通用:文档解析/Excel/日志)
    ↓
config.py  (配置:所有路径/API Key/阈值,全部可 env 覆盖)
```

- **入口双形态**:CLI (`python -m src.main --message "..."`) + FastAPI HTTP (`POST /process`)
- **配置集中化**:14 个配置项,任意一个改 `config.py` 或 `.env` 即可生效,零改动散落
- **模块可独立测试**:classifier / submission / qa 各自有纯函数 + pytest 单元测试,不启动不写文件

---

## B. RAG 检索设计

**三级混合检索,不用向量模型:**

```
学生问题
  │
  ├─[1] FAQRetriever: BM25 on Q (jieba 分词)
  │         命中阈值 ≥ 4.0 → 直接 LLM 润色
  │
  ├─[2] BM25Retriever: BM25 on 文档分块 (jieba 分词)
  │         取 top-5 片段拼 context
  │
  └─[3] LLM 兜底: prompt = context + 问题
```

**分块策略:**
- 按段落(空行分隔) + 句末标点(。！？；)递归切分
- 目标块 400 字,重叠 50 字
- 兼顾上下文连贯性

**支持的文档格式:**

| 格式 | 工具 | 说明 |
|------|------|------|
| PDF | pdfplumber | 按页提取,页码做元信息 |
| DOCX | python-docx | 段落 + 表格内容 |
| DOC | LibreOffice headless | 先转 DOCX 再读 |
| XLSX | openpyxl | 每个单元格拼成`sheet:row:col:内容` |
| TXT/MD | 原生读取 | UTF-8 |

**索引持久化:** pickle + BM25,资料更新才跑 `scripts/build_index.py`,实测 232 个片段<!-- 以实际构建日志为准 -->(来自真实部署日志)。

---

## C. 集成与部署工程

**跨平台开发-生产一致性:**
- **开发**:Windows 11 + VS Code
- **生产**:WSL Ubuntu,代码通过 rsync 单向同步(`scripts/deploy.sh`)
- `deploy.sh` 排除 .venv / logs / index,保留 .env(已有凭据不覆盖)

**子进程 IPC 契约(踩坑点):**

OpenClaw TS 端通过 `spawn(cmd, [...args])` 调用 Python bridge:

```python
# TS 端(OpenClaw)
const r = spawn("python", [
  "scripts/openclaw_bridge.py",
  "--message", rawMsg,   // 数组形式,不过 shell
]);
const data = JSON.parse(r.stdout);  // 只读 stdout
if (r.exitCode !== 0) handleError();
```

**踩坑 1:子进程 stdout 被污染**
- **问题**:jieba 的 `print()` 和 `src/utils/*.py` 里的 `print("[xxx]")` 会打到 stdout,TS 端 `JSON.parse` 爆掉
- **解决**:在 `process()` 执行期间用 `contextlib.redirect_stdout(sys.stderr)` 临时接管 stdout,所有 `src/` 模块改用 `stderr_log.warn()`,只在最后恢复 stdout 写一行 JSON
- **回归护栏**:加 `test_bridge_stdout_clean.py`,以后谁在 `src/` 加 print 立刻失败

**踩坑 2:TS execSync 模板字符串拼接中文参数失败**
- **问题**:`execSync(`python ... --message "${msg}")`) 在 Node.js 对某些 Unicode 字符转义出错
- **解决**:改用 `spawn` + 数组传参,参数不经过 shell 解释

**踩坑 3:Unicode 前缀变体不匹配**
- **问题**:OpenClaw 透传的消息里前缀字符可能被 normalize(如 `【` U+3010 vs `[` U+FF3B)
- **现象**:始终返回 `{"type":"skip"}`
- **诊断**:在 `process()` 入口打 `first_20_codepoints` 调试日志,一眼 diff 看出哪个字符变了
- **解决**:前缀支持列表 `["【转发学生提问】","【转发同学提问】","[转发学生提问]"]`,`.env` 可配

**踩坑 4:WSL 访问 Windows 中文路径编码异常**
- **现象**:`FileNotFoundError: ...雨课堂问题...`
- **解决**:在 Windows CMD 建英文软链接 `mklink /D ykt_questions 雨课堂问题`,配置 `WINDOWS_ROOT=/mnt/c/Users/.../ykt_questions`

**日志设计:**
- JSON Lines 格式,按月分文件(`data/logs/YYYY-MM.log`)
- 每条记录含:timestamp / event / type / message / 处理耗时
- 便于 grep 实时排查 + 增量统计

---

## D. 测试与质量保障

| 测试文件 | 覆盖内容 | 用例数 |
|---------|---------|-------|
| `test_classifier.py` | 关键词规则、补交/提问分类 | 5 |
| `test_submission.py` | 学号抽取、姓名反查、作业次数 | 5 |
| `test_faq.py` | FAQ 解析、BM25 检索评分 | 3 |
| `test_chunker.py` | 文本分块、块大小、重叠 | 4 |
| `test_main.py` | 前缀路由、变体匹配、空消息 | 10 |
| `test_bridge_stdout_clean.py` | stdout 清洁、JSON 格式 | 3 |
| **合计** | | **30** |

**重点:回归护栏 `test_bridge_stdout_clean.py`:**
```python
def test_bridge_stdout_is_pure_json():
    r = subprocess.run([python, "openclaw_bridge.py", "--message", "你好"])
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    data = json.loads(lines[0])  # 爆掉 = 有污染
    assert "type" in data and "reply" in data
```

---

## 简历可用句

### 30 字版
> 基于 BM25 RAG + 规则分类的微信助教 Bot,实现作业补交自动登记与 FAQ 智能答疑。

### 100 字版
> 独立完成课程助教自动化系统,采用 BM25 三级混合检索(FAQ→资料碎片→LLM 兜底),配合前缀路由隔离闲聊消息;针对子进程 IPC stdout 污染问题,设计 redirect_stdout 方案并编写回归测试;全栈 Python,配置驱动,跨 Windows/WSL 双平台部署。

### 3 分钟面试讲稿版

**项目背景**:我是中山大学数电实验课的助教,每周手工处理 20+ 条作业补交登记和重复答疑,累且容易出错。

**我的方案**:做了一个"微信 Bot → 本地 Python 服务"的全自动 pipeline。学生发消息给 Bot,Bot 转发给我写的 Python 程序,程序自动分类:是补交就写 Excel,是问题就走 FAQ/RAG/LLM 三级检索生成答复。

**技术难点 1 — 子进程 IPC 污染**:OpenClaw 是 Node.js,调 Python 用 `spawn`。问题是 jieba 和我自己写的 `print()` 会污染 stdout,TS 端 `JSON.parse` 直接爆掉。我用 `contextlib.redirect_stdout` 在 `process()` 执行期间把 stdout 重定向到 stderr,只在最后写一行 JSON,还加了专门的回归测试。

**技术难点 2 — 中文检索**:没用向量模型(太重),用的是 BM25 + jieba 分词。文档按段落 + 句末标点切分,块大小 400 字,重叠 50 字。FAQ 单独建索引每次启动现建(数据量小,快)。资料多才用持久化的 pickle 索引。

**工程思考**:配置全放 `config.py`,任意改 `.env` 不用动代码;跨平台(Windows 开发/WSL 生产),代码单向 rsync 同步;所有 `src/` 模块禁止 `print()` 写到 stdout,统一走 `stderr_log`,防止再污染。
