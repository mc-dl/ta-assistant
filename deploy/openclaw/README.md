# deploy/openclaw —— 把 ta-assistant 接进 OpenClaw

这个目录放的是**接入件**(不是运行时代码):它们是装到 OpenClaw 那侧的"配置文件",
让 OpenClaw 的微信 agent 知道"带转发前缀的消息要交给 ta-assistant 处理"。

## 目录内容

| 文件 | 装到哪里 | 作用 |
|---|---|---|
| `workspace/skills/ta-assistant/SKILL.md` | `~/.openclaw/workspace/skills/ta-assistant/SKILL.md` | 技能说明:什么时候调、怎么调、返回的 JSON 怎么处理 |
| `workspace/AGENTS.ta-assistant.md` | 追加到 `~/.openclaw/workspace/AGENTS.md` 末尾 | 路由铁律:带前缀的消息不许自己回答,必须走技能 |

## 一键安装

在 WSL 里:

```bash
cd ~/ta-assistant
bash scripts/install_openclaw_skill.sh
```

脚本是幂等的:技能文件覆盖更新,AGENTS.md 只在没有标记时追加一次(追加前自动备份),
最后自动跑一次 bridge 自检。

## 为什么用"技能 + AGENTS.md",而不是改 OpenClaw 源码?

OpenClaw 的 agent 本身就是一个能跑 shell 的智能体,它有 bash 工具、也支持 workspace 技能。
所以"接入"的正确姿势不是改它的代码,而是**用它的扩展点告诉它该调什么**:

```
微信消息 → openclaw-weixin 插件 → agent(main 会话)
                                      │ 读到 AGENTS.md 的路由规则 + ta-assistant 技能
                                      ▼
                            bash: <venv>/python scripts/openclaw_bridge.py --message "<原文>"
                                      │ stdout 一行 JSON
                                      ▼
                            把 JSON.reply 原样发回微信
```

好处:不动 OpenClaw 的配置结构、不怕它升级覆盖、接入件还能进版本库随项目同步。

## 升级路径(可选)

如果以后想要**完全确定性**的转发(不经过大模型判断),可以改用 MCP 工具方案:

1. 在 ta-assistant 里加一个 MCP stdio server,暴露工具 `ta_assistant_process(message)`
2. `openclaw mcp add ...` 把该 server 注册进去
3. 把 AGENTS.md 里的"跑 shell"改成"调用工具"

代价是多一个进程和一份 MCP 依赖;当前技能方案已够用,先不引入。
