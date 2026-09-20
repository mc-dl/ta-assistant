# 交接文档

> 更新日期:2026-04-21

## 一、目录结构重组(上次遗留)

| 原路径 | 新路径 | 原因 |
|--------|--------|------|
| `src/classifier.py` | `src/handlers/classifier.py` | handlers 应为独立包 |
| `src/submission.py` | `src/handlers/submission.py` | 同上 |
| `src/qa.py` | `src/handlers/qa.py` | 同上 |
| `src/minmax.py` | `src/llm/minmax.py` | llm 应为独立包 |
| `src/doc_parser.py` | `src/utils/doc_parser.py` | utils 工具集 |

新增:
- `src/handlers/__init__.py`
- `src/llm/__init__.py`

---

## 二、本次改动(2026-04-20)

### 任务 1:消息前缀过滤 `【转发学生提问】`

**修改的文件:**

| 文件 | 改动 |
|------|------|
| `src/config.py` | 新增 `STUDENT_FORWARD_PREFIX` 常量,支持 `.env` 的 `FORWARD_PREFIX` 覆盖 |
| `src/main.py` | `process()` 最前面加前缀检查;无前缀或空消息 → `type=skip` |
| `tests/test_main.py` | **新建** 5 个测试用例覆盖前缀过滤逻辑 |
| `docs/requirements.md` | F-1 改为"消息前缀过滤",F-2~F-5 顺延为 F-3~F-6;验收标准加前缀 |
| `docs/README.md` | 快速上手加第 5 步 deploy.sh;加"消息前缀过滤"说明章节 |
| `env.example` | 加 `# FORWARD_PREFIX=【转发学生提问】` 说明行 |

**核心逻辑:**
```python
# src/main.py process()
prefix = config.STUDENT_FORWARD_PREFIX
if not message.startswith(prefix):
    return {"type": "skip", "reply": "(非学生消息,未处理)"}
msg = message[len(prefix):].strip()
if not msg:
    return {"type": "skip", "reply": "(消息为空,未处理)"}
# 继续原有分类 → handler 逻辑...
```

---

### 任务 2:一键部署脚本

**新建文件:**

| 文件 | 说明 |
|------|------|
| `scripts/deploy.sh` | 从 Windows rsync 同步 → 确保 .env → setup.sh → build_index.py |

**修改的文件:**
- `scripts/setup.sh` — 加了"`.venv 已存在则跳过重建`"的提示,加速重复部署

**deploy.sh 逻辑:**
```bash
# 1/4 rsync 同步(排除 .venv, data/logs, data/index, .env)
# 2/4 首次部署才复制 .env(不覆盖已有凭据)
# 3/4 bash setup.sh(已安装则只跑 pip install)
# 4/4 python scripts/build_index.py
```

**使用方式:**
```bash
# 在 WSL Ubuntu 里
bash /mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant/scripts/deploy.sh
```

---

### 任务 3:确认配置路径

`src/config.py` 已更新:
```python
WINDOWS_ROOT = Path(os.getenv("WINDOWS_ROOT", "/mnt/c/Users/YOUR_USERNAME/Downloads/ykt_questions"))
MATERIALS_DIR = WINDOWS_ROOT / "materials"  # ← 注意:数据资料目录名
```

⚠️ 你的 `ykt_questions` 目录里,资料子目录是不是 `materials`?如果不是请改 `config.py` 里的 `MATERIALS_DIR`。

---

## 三、测试结果

### 单元测试 (22 passed)

```
pytest test/ -v
============================= test session starts =============================
test/test_chunker.py::test_split_short_text PASSED
test/test_chunker.py::test_split_empty PASSED
test/test_chunker.py::test_split_respects_size PASSED
test/test_chunker.py::test_split_preserves_content PASSED
test/test_classifier.py::test_submission_with_id_and_keyword PASSED
test/test_classifier.py::test_submission_various_keywords PASSED
test/test_classifier.py::test_question_with_mark PASSED
test/test_classifier.py::test_question_with_keywords PASSED
test/test_classifier.py::test_empty_and_unknown PASSED
test/test_faq.py::test_parse_faq_basic PASSED
test/test_faq.py::test_faq_retriever_search PASSED
test/test_faq.py::test_faq_retriever_empty_query PASSED
test/test_main.py::TestPrefixFilter::test_submission_with_prefix PASSED
test/test_main.py::TestPrefixFilter::test_question_with_prefix PASSED
test/test_main.py::TestPrefixFilter::test_no_prefix_returns_skip PASSED
test/test_main.py::TestPrefixFilter::test_empty_message PASSED
test/test_main.py::TestPrefixFilter::test_prefix_preserved_in_log PASSED
test/test_submission.py::test_extract_student_id PASSED
test/test_submission.py::test_extract_name_from_roster PASSED
test/test_submission.py::test_extract_name_from_explicit_pattern PASSED
test/test_submission.py::test_extract_name_skip_stopwords PASSED
test/test_submission.py::test_extract_homework_number PASSED
============================= 22 passed in 0.76s =============================
```

### 冒烟测试

```bash
# 有前缀 → 正常处理
$ python -m src.main --message "【转发学生提问】Proteus 在哪下载?"
{"type": "question", "reply": "抱歉,知识库尚未构建...", "sources": []}

# 无前缀 → skip
$ python -m src.main --message "你好啊今天天气不错"
{"type": "skip", "reply": "(非学生消息,未处理)"}
```

---

## 四、还需要你手动做的事

### 1. 确认数据目录名

检查 `ykt_questions` 下的子目录名:

```
C:\Users\YOUR_USERNAME\Downloads\ykt_questions\
├── materials\         ← 教学资料放这里
├── 花名册\
│   ├── 电路基础理论课_学生名单.xlsx   ← 本学期的名单(雨课堂导出后改名)
│   └── 电路基础理论课_补交表.xlsx     ← 补交记录;由名单文件名推导,首次补交时自动建
└── 常问问题.txt
```

> 补交表**不在** `ykt_questions\` 根目录下,而是和名单同目录、同课名 ——
> 换课(覆盖名单文件)时它自动跟着换,不用记得改第二个地方。
> 推导规则见 `src/config.py` 的 `submission_table_for()`。

如果资料实际目录名不是 `materials`,改 `src/config.py` 里的 `MATERIALS_DIR`。

### 2. 放数据文件

确保以下文件/目录存在:
- `ykt_questions/materials/` 下有教学资料(PDF/DOCX)
- `ykt_questions/常问问题.txt` (FAQ 文件)
- `ykt_questions/花名册/电路基础理论课_学生名单.xlsx` (名单,即 `config.GRADEBOOK_PATH`)
  —— 放好后跑 `.venv/bin/python scripts/check_roster.py`,**退出码 0 才算可用**
- `ykt_questions/花名册/电路基础理论课_补交表.xlsx` (补交记录) **不用手工准备**:
  它由名单文件名推导、首次补交时自动创建

### 3. 构建索引

```bash
source .venv/bin/activate
python scripts/build_index.py
```

### 4. `.env` 已就绪

`.env` 里已有 MinMax 凭据(上次配的),如需修改直接编辑。

---

## 五、文件变更清单

### 修改的文件(11 个)
```
docs/README.md          # 快速上手加 deploy.sh / 前缀过滤说明
docs/requirements.md    # F-1 新增前缀过滤 / 重新编号 / 验收标准加前缀
src/config.py          # STUDENT_FORWARD_PREFIX 常量
src/main.py             # 前缀过滤逻辑
scripts/setup.sh       # .venv 存在则跳过重建
env.example            # 加 FORWARD_PREFIX 说明
```

### 新建的文件(2 个)
```
test/test_main.py       # 前缀过滤测试(5 个用例)
scripts/deploy.sh      # 一键部署脚本
```

---

## 六、已知风险

1. **`materials` 目录名待确认** — 如果你资料放在别的名字(如 `数电资料`),需要改 `config.MATERIALS_DIR`
2. **WSL 里 deploy.sh 需要 rsync** — 如果 WSL 没装 rsync: `sudo apt install rsync`
3. **补交重复检测** — 24 小时时间窗口逻辑未实现,重复提交会追加记录而非标记"重复"

---

## 七、本次改动(2026-04-21) — 前缀容错 + 调试增强

### 问题背景

测试发现 `【转发学生提问】Proteus 在哪下载?` 被判断为 `(非学生消息,未处理)`。
怀疑原因:(a) OpenClaw TS 层把前缀剥掉;(b) 微信/TS 层做了字符 normalize(全角半角、U+3010 vs U+FF3B 等)。

### 改动清单

| 文件 | 改动 |
|------|------|
| `src/config.py` | `STUDENT_FORWARD_PREFIX` → `STUDENT_FORWARD_PREFIXES` 列表(4 个变体);`.env` 的 `FORWARD_PREFIX` 支持逗号分隔覆盖全部 |
| `src/main.py` | 新增 `_strip_forward_prefix()` 函数(容忍前导空格/换行);`process()` 开头加 `process_input` 调试日志,记录 `raw_repr` 和 `first_20_codepoints` |
| `src/handlers/classifier.py` | 加了一条注释说明补交流判定逻辑 |
| `test/test_main.py` | `TestPrefixVariants` 类新增 5 个测试 |
| `docs/deployment.md` | 故障排查表加了一条"非学生消息未处理"的排查指引 |
| `docs/README.md` | 前缀过滤章节列出全部 4 个变体;说明 `.env` 逗号分隔语法 |
| `env.example` | `FORWARD_PREFIX` 示例改为逗号分隔多前缀格式 |

**前缀变体:**

```python
STUDENT_FORWARD_PREFIXES = [
    "【转发学生提问】",   # 标准
    "【转发同学提问】",   # 同学变体
    "【转发学生消息】",   # 消息变体
    "[转发学生提问]",     # 半角
]
```

`.env` 覆盖: `FORWARD_PREFIX=【转发学生提问】,【转发同学提问】` (覆盖全部默认变体)

### 测试结果

**27 passed:**

```
test/test_main.py::TestPrefixFilter::test_submission_with_prefix PASSED
test/test_main.py::TestPrefixFilter::test_question_with_prefix PASSED
test/test_main.py::TestPrefixFilter::test_no_prefix_returns_skip PASSED
test/test_main.py::TestPrefixFilter::test_empty_message PASSED
test/test_main.py::TestPrefixFilter::test_prefix_preserved_in_log PASSED
test/test_main.py::TestPrefixVariants::test_standard_prefix_question PASSED
test/test_main.py::TestPrefixVariants::test_variant_prefix_classmate PASSED
test/test_main.py::TestPrefixVariants::test_prefix_with_leading_space PASSED
test/test_main.py::TestPrefixVariants::test_prefix_submission PASSED
test/test_main.py::TestPrefixVariants::test_no_prefix_returns_skip PASSED
```

**冒烟测试:**

```bash
$ python -m src.main --message "【转发学生提问】Proteus 在哪下载?"
→ {"type": "question", ...} ✓

$ python -m src.main --message " 【转发同学提问】什么是竞争冒险?"
→ {"type": "question", ...} ✓  (前导空格+变体前缀均正确匹配)

$ python -m src.main --message "你好"
→ {"type": "skip", "reply": "(非学生消息,未处理)"} ✓
```

### 下次 OpenClaw 同步时需确认的事

1. **OpenClaw TS 代码**传给 bridge 的 `--message` 参数,内容是否带有 `【转发学生提问】` 这 8 个字符?去 `data/logs/` 查最新的 `process_input` 事件,对比 `raw_repr` 和 `first_20_codepoints`。
2. 如果 OpenClaw 确实会剥前缀,那需要在 **OpenClaw 端** 加上前缀再传给 bridge,或者改 `config.py` 里的 `STUDENT_FORWARD_PREFIXES` 为空列表+在 OpenClaw 层统一加前缀。
3. 如果微信发了全角/半角变体(如 `［转发学生提问］` U+FF3B),对比 codepoint 后在 `.env` 加 `FORWARD_PREFIX=...,［转发学生提问］` 即可。

---

## 八、本次改动(2026-04-21) — bridge stdout 防水

### 问题

`jieba` 的 `print()` 和 `src/utils/*.py` 里的 `print("[xxx] ...")` 打到 stdout,导致 bridge 的 JSON 输出被污染,OpenClaw TS 端 `JSON.parse` 失败。

### 改动清单

| 文件 | 改动 |
|------|------|
| `scripts/openclaw_bridge.py` | 重写,`process()` 执行期间用 `contextlib.redirect_stdout(sys.stderr)` 临时接管 stdout;只在最后写一行 JSON;加 jieba 日志抑制 |
| `src/utils/stderr_log.py` | **新建**,`_warn()` 函数统一写 stderr |
| `src/utils/excel_ops.py` | `print("[excel_ops] ...") `→ `_warn(...)` |
| `src/utils/doc_parser.py` | 同上 |
| `src/rag/retriever.py` | 同上 |
| `src/rag/indexer.py` | 同上 |
| `src/main.py` | CLI 的 `print(json)` → `print(json, file=sys.stderr)` |
| `test/test_bridge_stdout_clean.py` | **新建**,3 个测试验证 bridge stdout 干净 |

### 验证

**30 passed:**

```
pytest test/ -v
...
test/test_bridge_stdout_clean.py::test_bridge_stdout_is_pure_json PASSED
test/test_bridge_stdout_clean.py::test_bridge_stdout_clean_with_prefix PASSED
test/test_bridge_stdout_clean.py::test_bridge_no_extra_newlines PASSED
```

**bridge 验证(Windows CMD 会因 GBK 代码页显示乱码,但 JSON 本身正确):**

```bash
$ python scripts/openclaw_bridge.py --message "【转发学生提问】Proteus 在哪下载?" 2>/dev/null
{"type": "question", "reply": "...知识库尚未构建...", "sources": []}   # 仅此一行 ✓

$ PYTHONIOENCODING=utf-8 python -c "
  import subprocess, sys, json
  r = subprocess.run([sys.executable, 'scripts/openclaw_bridge.py',
      '--message', '【转发学生提问】Proteus 在哪下载?'],
      capture_output=True, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
  print('stdout lines:', len([l for l in r.stdout.splitlines() if l.strip()]))
  print('JSON OK:', json.loads(r.stdout.strip()))
"
stdout lines: 1
JSON OK: {'type': 'question', ...}  # 纯 JSON,无污染 ✓
```

**stdout 清洁合同:**
- `scripts/openclaw_bridge.py` 永远只向 stdout 写**一行** JSON(末尾 `\n`)
- `src/` 下所有模块禁止 `print()` 到 stdout,统一用 `stderr_log._warn()`
- `test/test_bridge_stdout_clean.py` 是回归护栏


