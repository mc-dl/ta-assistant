# 数电助教自动化助手 (TA Assistant)

> 一个轻量级的数电课助教工作自动化工具,配合 OpenClaw 微信 Bot 使用,基于 RAG + 规则分类实现作业补交自动登记 + 学生提问智能回答。

> ⚠️ **时效性(2026-09-20 校订)**
> 这是 **2026-04 版的 README**。项目现在的 README 在**仓库根目录 [`../README.md`](../README.md)**,
> 那份才是权威。本文**保留原样**当作"当时怎么写的"的记录,但**下面几条照做会出错**:
>
> | 本文写的 | 现状 |
> |---|---|
> | 项目目标、快速上手 5 步 | 照做会装错 —— 先看根 [`README.md`](../README.md) 的「快速开始」 |
> | 运行环境"文件通过 `/mnt/c/...` 访问 Windows 路径" | 代码和数据都在 **WSL 原生目录**(`~/ta-assistant/` + `~/ykt_questions/`),`/mnt/c` 那份只是**编辑源码的工作副本**;见 [`deployment.md`](deployment.md) §0 |
> | LLM:MinMax API(已配置) | 默认通道已换成 **OpenCode Go**;MinMax 降为**回滚通道**;见 [`design.md`](design.md) §3.7 |
> | 检索:"BM25 + 可选 sentence-transformers 升级" | 就是 BM25(不需要向量模型),但 **IDF 换成了 Lucene 写法**;见 [`knowledge_base.md`](knowledge_base.md) §3.6 |
> | 脚本树里没有 `md2html.py`(把文档转成 HTML 那个) | 完整清单**以根 README 的树为准** —— 这两棵树对不上时,信根目录那份 |
> | 没有「测试」一节 | 现在有 **389 个测试** + 检索评测基线,见根 README 的「测试」 |
> | 没有 `src/handlers/record.py`、`src/utils/course_facts.py` | 后来加的:`【记录】`(助教在微信里写 FAQ)与课程事务事实表,见 [`knowledge_base.md`](knowledge_base.md) §5.5 / §6.6 |
> | LLM 只有 MinMax | 默认通道是 **OpenCode Go**,MinMax 降为回滚通道,见 [`design.md`](design.md) §3.7 |
> | 目录树只列了 5 个 md | 还有 `wechat_end_to_end.md` / `knowledge_base.md`,且**每篇都有网页版 `docs/*.html`** —— 见 [`index.html`](index.html) |
> | 没提行尾要求 | 全仓文本文件必须是 **LF**(`.gitattributes` 钉住)。CRLF 会被 rsync 原样带到 WSL,`setup.sh` 会直接跑不起来(报错还指向文件末尾),见 [`deployment.md`](deployment.md) §7「行尾必须是 LF」 |
>
> (目录树里的 `data/samples/` 之类的**仍然准确**,不用疑心。)

:::stats
- **169** | 在册学生 | 电路基础理论,8 个行政班
- **3165** | 索引块 | 服务两门课:数电实验 + 电路基础理论
- **389** | 测试 | `pytest test/` 全绿才允许提交
- **17/17** | 检索命中 | 评测闸门,MRR 0.956;退化就退出码 1
:::

> 上面这些数字是**现状**;下面正文是**当时**。两者冲突时,以根目录
> [`../README.md`](../README.md) 和 [`knowledge_base.md`](knowledge_base.md) 为准。

:::timeline
- 2026-04-19 | 项目启动 | 只有数电实验一门课,需求 / 设计 / 部署三份文档 + 骨架代码
- 2026-09-01 | 加第二门课 | 电路基础理论进语料库,答疑要能分得清是哪门课
- 2026-09-20 | 校订 + 换学期 | 八份文档逐处改成本文的实际值;同一天踩到"花名册读不出来"
- 2026-09-21 | 补交表按课推导 | 换名单这一处动作,同时换掉花名册和补交表
:::

## 项目目标

作为中山大学数字电路与逻辑设计实验课助教,每周要处理:

1. **作业补交**:学生在微信里发"我作业漏交了",发截图、姓名、学号。需要手工登记到 Excel。
2. **答疑**:学生问重复性问题(Proteus 在哪下载、PPT 在哪里、作业怎么批改等)或数电课本相关问题。需要翻资料、打字回复。

本项目通过 OpenClaw(微信 Bot)+ 本地 Python 服务实现全自动化。

## 快速上手(5 步)

:::steps
- 阅读文档 | `docs/requirements.md` 和 `docs/design.md`
- 放置项目 | 把整个 `ta-assistant/` 文件夹移到 `C:\Users\YOUR_USERNAME\Downloads\`
- 交给 Claude Code | 打开 Claude Code,粘贴 `docs/claude_code_prompt.md` 里的内容,让它自动完成剩余实现和环境配置
- WSL 首次部署 | 在 WSL Ubuntu 里跑 `bash /mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant/scripts/deploy.sh`
- 接入 OpenClaw | `bash scripts/install_openclaw_skill.sh`;完整链路与排障见 [`wechat_end_to_end.md`](wechat_end_to_end.md)
:::

> ⚠️ 这 5 步是**当时**的交付步骤,现在照做会装错(第 2 步的路径、第 4 步的脚本都变了)。
> 真实的快速开始看根目录 [`../README.md`](../README.md);这里原样保留,
> 是为了留下"当时打算怎么交付"这份记录。

## 目录结构

```
ta-assistant/
├── README.md                   本文件
├── docs/                       所有设计文档
│   ├── index.html              **文档首页(导航页)**,双击看这个
│   ├── requirements.md         需求文档(PRD)
│   ├── design.md               系统设计文档
│   ├── deployment.md           部署 & OpenClaw 接入指南
│   ├── wechat_end_to_end.md    微信端到端跑通手册(实测记录)
│   ├── knowledge_base.md       知识库说明(资料/FAQ 来源、重建步骤、已知限制)
│   ├── claude_code_prompt.md   交给 Claude Code 的 prompt
│   └── *.html                  上面每一篇的**网页版**(`scripts/md2html.py` 生成,别手改)
├── src/                        源代码
│   ├── config.py               配置(路径、API Key)
│   ├── main.py                 主入口(命令行 & HTTP)
│   ├── handlers/               业务处理器
│   │   ├── classifier.py       消息分类:补交 / 提问 / 其他
│   │   ├── submission.py       补交登记逻辑
│   │   └── qa.py               答疑逻辑
│   ├── rag/                    检索增强
│   │   ├── indexer.py          构建知识库索引
│   │   └── retriever.py        从索引检索相关片段
│   ├── llm/
│   │   └── minmax.py           MinMax API 封装
│   └── utils/
│       ├── doc_parser.py       解析 pdf / docx / doc / xlsx / pptx / txt
│       └── excel_ops.py        读写 Excel
├── scripts/
│   ├── build_index.py          一次性:构建 RAG 索引
│   ├── import_circuit_basic.py 英文版作业答案 → 带中文锚点的语料
│   ├── import_circuit_theory.py 中文版作业答案 + 课堂讲义 + 错误集锦 → 语料
│   ├── enhance_circuit_basic.py 调大模型补【中文详细解析】
│   ├── audit_enhanced_numbers.py 审计:解析里的数字有没有被改
│   ├── ocr_textbook.py         扫描版教材 OCR(需 /home/jj/ocr-venv)
│   ├── import_textbook.py      OCR 结果 → 分章语料 + 印刷页码标记
│   ├── eval_retrieval.py       检索质量评测(--save-baseline / --compare)
│   ├── openclaw_bridge.py      OpenClaw 调用入口
│   ├── setup.sh                一键安装依赖(WSL Ubuntu)
│   └── deploy.sh               一键部署脚本(WSL,同步代码+装依赖+建索引)
├── data/
│   ├── index/                  RAG 索引(自动生成)
│   ├── logs/                   运行日志
│   └── samples/                测试样本
├── test/                       单元测试
├── requirements.txt            Python 依赖
├── .env.example                环境变量模板
└── .gitignore
```

## 技术栈

- **语言**:Python 3.10+
- **运行环境**:WSL Ubuntu(文件通过 `/mnt/c/...` 访问 Windows 路径)
- **文档解析**:`pdfplumber` / `python-docx` / `openpyxl`
- **OCR**(仅扫描版教材,独立 venv):RapidOCR(ONNX Runtime),纯 pip、CPU 可跑
- **检索**:BM25(轻量,无需下载模型) + 可选 sentence-transformers 升级
- **LLM**:MinMax API(已配置)
- **接入层**:OpenClaw(微信)→ 本项目

## 为什么这样设计

- **文档先行**:所有需求和设计写清楚,Claude Code 才知道做什么
- **BM25 优先**:比向量检索简单 10 倍,无需下载模型,对中文效果不差
- **模块化**:分类器/补交/答疑解耦,后续可以单独迭代
- **路径集中**:所有路径在 `config.py` 一处配置,改一次到处生效

## 消息前缀过滤

系统只处理带以下**任一前缀**的消息,其他消息(闲聊、通知等)直接跳过返回 `{"type": "skip"}`。
这确保 OpenClaw 转发的都是学生消息,不会把闲聊也当业务处理。

**支持的前缀变体:**

| 前缀 | 说明 |
|------|------|
| `【转发学生提问】` | 标准前缀 |
| `【转发同学提问】` | 兼容"同学"打字习惯 |
| `【转发学生消息】` | 兼容"消息"变体 |
| `[转发学生提问]` | 半角方括号兼容 |

前缀前允许有前导空格(系统会自动 lstrip)。

可通过 `.env` 的 `FORWARD_PREFIX`(逗号分隔,覆盖全部)或 `src/config.py` 的 `STUDENT_FORWARD_PREFIXES` 列表修改/增删前缀变体。

## 开发约定

- **`src/` 下严禁 print 到 stdout**。所有非致命调试/信息输出必须用 `from src.utils.stderr_log import warn as _warn; _warn("...")` 输出到 stderr。stdout 是 OpenClaw JSON 解析的唯一通道,禁止污染。
- **`scripts/openclaw_bridge.py`** 会在执行 `process()` 期间把 stdout 重定向到 stderr,确保最终只向 OpenClaw 写一行纯 JSON。
- **回归测试**:运行 `pytest test/test_bridge_stdout_clean.py -v` 可验证 bridge stdout 干净。
