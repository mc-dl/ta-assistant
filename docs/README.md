# 数电助教自动化助手 (TA Assistant)

> 一个轻量级的数电课助教工作自动化工具,配合 OpenClaw 微信 Bot 使用,基于 RAG + 规则分类实现作业补交自动登记 + 学生提问智能回答。

## 项目目标

作为中山大学数字电路与逻辑设计实验课助教,每周要处理:

1. **作业补交**:学生在微信里发"我作业漏交了",发截图、姓名、学号。需要手工登记到 Excel。
2. **答疑**:学生问重复性问题(Proteus 在哪下载、PPT 在哪里、作业怎么批改等)或数电课本相关问题。需要翻资料、打字回复。

本项目通过 OpenClaw(微信 Bot)+ 本地 Python 服务实现全自动化。

## 快速上手(5 步)

1. **阅读文档**:`docs/requirements.md` 和 `docs/design.md`
2. **放置项目**:将整个 `ta-assistant/` 文件夹移动到 `C:\Users\YOUR_USERNAME\Downloads\`
3. **交给 Claude Code**:打开 Claude Code,粘贴 `docs/claude_code_prompt.md` 里的内容,让它自动完成剩余实现和环境配置
4. **WSL 首次部署**:在 WSL Ubuntu 里运行 `bash /mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant/scripts/deploy.sh`
5. **接入 OpenClaw**:按 `docs/deployment.md` 配置 OpenClaw 调用本项目

## 目录结构

```
ta-assistant/
├── README.md                   本文件
├── docs/                       所有设计文档
│   ├── requirements.md         需求文档(PRD)
│   ├── design.md               系统设计文档
│   ├── deployment.md           部署 & OpenClaw 接入指南
│   └── claude_code_prompt.md   交给 Claude Code 的 prompt
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
│       ├── doc_parser.py       解析 pdf / docx / doc / txt
│       └── excel_ops.py        读写 Excel
├── scripts/
│   ├── build_index.py          一次性:构建 RAG 索引
│   ├── openclaw_bridge.py      OpenClaw 调用入口
│   ├── setup.sh                一键安装依赖(WSL Ubuntu)
│   └── deploy.sh               一键部署脚本(WSL,同步代码+装依赖+建索引)
├── data/
│   ├── index/                  RAG 索引(自动生成)
│   ├── logs/                   运行日志
│   └── samples/                测试样本
├── tests/                      单元测试
├── requirements.txt            Python 依赖
├── .env.example                环境变量模板
└── .gitignore
```

## 技术栈

- **语言**:Python 3.10+
- **运行环境**:WSL Ubuntu(文件通过 `/mnt/c/...` 访问 Windows 路径)
- **文档解析**:`pdfplumber` / `python-docx` / `openpyxl`
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
