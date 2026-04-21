# 部署与 OpenClaw 接入指南

## 0. 目录放置

把整个 `ta-assistant/` 文件夹放到:
```
C:\Users\YOUR_USERNAME\Downloads\ta-assistant\
```
在 WSL Ubuntu 里对应路径:
```
/mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant/
```
**强烈建议**:在 WSL 内复制一份到 `$HOME`(避免中文路径 + 跨文件系统性能问题):
```bash
cp -r /mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant ~/ta-assistant
cd ~/ta-assistant
```
代码仍通过 `/mnt/c/...` 读写数据文件,但项目代码本身放 Linux 原生目录跑得更稳。

## 1. 环境安装(一次性)

```bash
cd ~/ta-assistant
bash scripts/setup.sh
```

`setup.sh` 会做:
1. `apt install libreoffice-core poppler-utils`(用来转 `.doc` 和 pdf 解析)
2. 创建 `.venv` 虚拟环境
3. `pip install -r requirements.txt`
4. 预下载 jieba 词典
5. 从 `.env.example` 复制一份 `.env`

然后编辑 `.env`,填入 MinMax 的凭据:
```
MINMAX_API_KEY=你的_API_KEY
MINMAX_GROUP_ID=你的_GROUP_ID
MINMAX_MODEL=abab6.5s-chat
```

## 2. 构建知识库索引(一次性 / 资料更新后)

```bash
cd ~/ta-assistant
source .venv/bin/activate
python scripts/build_index.py
```

成功后会看到:
```
扫描目录: /mnt/c/Users/YOUR_USERNAME/Downloads/雨课堂问题/数电资料
解析文件: 6 个  | 生成片段: 142 块
BM25 索引已保存: data/index/bm25_index.pkl
```

## 3. 本地冒烟测试

```bash
# 测试 1:补交
python -m src.main --message "我作业漏交了 张三 20230001 第1次"

# 测试 2:FAQ
python -m src.main --message "Proteus 在哪下载?"

# 测试 3:RAG
python -m src.main --message "什么是竞争冒险?怎么消除?"
```

每次都应该看到 JSON 返回和 `data/logs/YYYY-MM.log` 里新增一行。

## 4. OpenClaw 接入

### 方案 A:子进程调用(最简单,推荐先用这个)

在 OpenClaw 的"接收到助教转发"的钩子里,把整条消息透传给:
```bash
/home/<你的用户名>/ta-assistant/.venv/bin/python \
  /home/<你的用户名>/ta-assistant/scripts/openclaw_bridge.py \
  --message "<转发内容>"
```

脚本会在 `stdout` 打印 JSON:
```json
{"type": "question", "reply": "Proteus 需要自己找激活版..."}
```

OpenClaw 把 `reply` 字段发回微信即可。

### 方案 B:HTTP 服务(需要长连接)

启动:
```bash
cd ~/ta-assistant
source .venv/bin/activate
uvicorn src.main:app --host 127.0.0.1 --port 8765
```

OpenClaw 调用:
```
POST http://127.0.0.1:8765/process
Content-Type: application/json
{"message": "转发内容"}
```

响应:
```json
{"type": "submission", "reply": "✅ 已登记..."}
```

### 在 OpenClaw 端的伪代码示例

假设 OpenClaw 支持 Python 插件:
```python
import subprocess, json

def on_teacher_forward(message: str) -> str:
    result = subprocess.run(
        ["/home/ta/ta-assistant/.venv/bin/python",
         "/home/ta/ta-assistant/scripts/openclaw_bridge.py",
         "--message", message],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    return data["reply"]
```

## 5. 路径提示词(重要!)

你的 Windows 路径含中文、括号、全角字符,WSL 下**一定要用 bytes 处理或用 Path + utf-8 编码**。项目里所有路径已在 `src/config.py` 用 `Path` 封装,**不要**在 shell 里裸写中文路径,会挂。

如果路径真的有问题的临时应急方案:在 Windows 侧建一个英文软链接:
```cmd
mklink /D "C:\Users\YOUR_USERNAME\Downloads\ykt_questions" "C:\Users\YOUR_USERNAME\Downloads\雨课堂问题"
```
然后把 `config.py` 里的 `WINDOWS_ROOT` 改成 `/mnt/c/Users/YOUR_USERNAME/Downloads/ykt_questions`。

## 6. 日常维护

| 场景 | 操作 |
|---|---|
| 加新 FAQ | 在 `常问问题.txt` 里按 `Q:\nA:\n\n` 格式新增,**不需要**重建索引 |
| 加新数电资料 | 把文件拖进 `数电资料/`,再跑 `python scripts/build_index.py` |
| 补交表想导出 | 直接打开 Excel(路径见 `config.py`) |
| 看日志 | `tail -f ~/ta-assistant/data/logs/$(date +%Y-%m).log` |
| 清空当月日志 | `rm ~/ta-assistant/data/logs/$(date +%Y-%m).log` |

## 7. 故障排查

| 症状 | 原因 | 解决 |
|---|---|---|
| `FileNotFoundError: ...雨课堂问题...` | WSL 找不到中文路径 | 按 §5 做软链接 |
| `ImportError: no module named jieba` | 未激活 venv | `source .venv/bin/activate` |
| MinMax 401 | API Key 错 | 重新检查 `.env` |
| 回复全是"抱歉,知识库未就绪" | 没跑 build_index.py | 跑一次 |
| 补交表里姓名乱码 | Excel 用 GBK 打开 | 用 Excel 2019+ 或 WPS 打开 |
| 回复始终是"非学生消息,未处理" | OpenClaw 剥了前缀或字符被 normalize | 查 `data/logs/` 的 `process_input` 事件,对比 `first_20_codepoints` 与 `prefix_used` 的 Unicode;很可能是 OpenClaw TS 层把前缀字符改了或提前剥掉了 |
| OpenClaw 报 `bridge exited 0: ...日志内容...`? | TS 端把 stderr 当错误了 | bridge 侧保证 stdout 是纯 JSON;TS 侧应只看 exit code + JSON.parse,不要拼接 stderr 内容 |

## 8. 从零到上线的时间预估

| 步骤 | 预估时间 |
|---|---|
| 把项目文件夹放进 Downloads | 30 秒 |
| 粘贴 `claude_code_prompt.md` 让 Claude Code 补完实现 | 10-20 分钟 |
| 跑 `setup.sh` 安装依赖 | 5-10 分钟 |
| 配 `.env` 里的 MinMax 凭据 | 2 分钟 |
| 跑 `build_index.py` 建索引 | 1-2 分钟 |
| 冒烟测试 3 条消息 | 2 分钟 |
| OpenClaw 接入调通 | 10-20 分钟 |
| **总计** | **30-60 分钟** |
