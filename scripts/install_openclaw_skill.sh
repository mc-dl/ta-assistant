#!/usr/bin/env bash
# 把 ta-assistant 的转发路由"接入件"装到 OpenClaw 的 agent workspace。
#
# 做三件事(全部幂等,可以反复跑):
#   1. 拷技能   deploy/openclaw/workspace/skills/ta-assistant/SKILL.md → $WORKSPACE/skills/ta-assistant/SKILL.md
#   2. 追加路由规则到 $WORKSPACE/AGENTS.md(已存在标记则跳过;改动前自动备份)
#   3. 冒烟自检:跑一次 bridge,确认后端可用
#
# 用法(在 WSL 里):
#   bash ~/ta-assistant/scripts/install_openclaw_skill.sh
#   WORKSPACE=/path/to/workspace bash scripts/install_openclaw_skill.sh   # 自定义 workspace
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_SKILL="$ROOT/deploy/openclaw/workspace/skills/ta-assistant/SKILL.md"
SRC_SNIPPET="$ROOT/deploy/openclaw/workspace/AGENTS.ta-assistant.md"
WORKSPACE="${WORKSPACE:-$HOME/.openclaw/workspace}"
MARKER="学生转发消息路由(ta-assistant)"
# 路由规则的版本。**改 AGENTS.ta-assistant.md 的内容就要一起改这里**,
# 否则已经在跑的 workspace 里那段旧规则永远不会被更新 ——
# 脚本不会去改 workspace 里的文件(那是用户的),只会在这里提醒。
# v2 = 2026-09-20,加了助教的 【记录】 指令和第五种返回值 record。
VERSION="ta-assistant-route: v2"
PY="$ROOT/.venv/bin/python"

echo "==> 项目根目录 : $ROOT"
echo "==> 目标 workspace: $WORKSPACE"

if [ ! -d "$WORKSPACE" ]; then
  echo "!! workspace 不存在:$WORKSPACE" >&2
  echo "   确认 OpenClaw 装好、agent 跑过一次,或用 WORKSPACE=... 指定" >&2
  exit 1
fi

# ── 1. 技能文件 ───────────────────────────────────────────────
mkdir -p "$WORKSPACE/skills/ta-assistant"
cp "$SRC_SKILL" "$WORKSPACE/skills/ta-assistant/SKILL.md"
echo "==> [1/3] 技能已装:$WORKSPACE/skills/ta-assistant/SKILL.md"

# ── 2. AGENTS.md 路由规则(追加,不覆盖)────────────────────────
AGENTS="$WORKSPACE/AGENTS.md"
STALE=0
if [ ! -f "$AGENTS" ]; then
  echo "!! 找不到 $AGENTS,跳过路由规则写入" >&2
elif grep -qF "$MARKER" "$AGENTS"; then
  if grep -qF "$VERSION" "$AGENTS"; then
    echo "==> [2/3] AGENTS.md 里已有路由规则,且是当前版本($VERSION),跳过"
  else
    # 只提醒、不代改:AGENTS.md 是用户自己的文件,脚本不去动它。
    # 但必须**大声**说 —— 旧段落里的返回值表只有四种、也没有【记录】这条指令,
    # 装完技能文件却留着旧路由 = 助教发【记录】时 agent 会当聊天处理,静默失效。
    STALE=1
    echo "!! AGENTS.md 里的路由规则是**旧版本** —— 缺【记录】指令和 record 返回值。" >&2
    echo "   请手动删掉那一整段(从 '## 📨 学生转发消息路由' 到本节末尾),再重跑本脚本。" >&2
    echo "   (脚本不代改:AGENTS.md 是你的文件,里面可能还有你自己的内容)" >&2
  fi
else
  cp "$AGENTS" "$AGENTS.bak.$(date +%Y%m%d-%H%M%S)"
  printf '\n' >> "$AGENTS"
  cat "$SRC_SNIPPET" >> "$AGENTS"
  echo "==> [2/3] 路由规则已追加到 AGENTS.md(旧文件已备份为 AGENTS.md.bak.*)"
fi

# ── 3. 冒烟自检 ───────────────────────────────────────────────
echo "==> [3/3] 后端自检:"
set +e
OUT="$("$PY" "$ROOT/scripts/openclaw_bridge.py" --message '【转发学生提问】Proteus 在哪下载?' 2>/dev/null)"
RC=$?
set -e
if [ "$RC" -ne 0 ] || [ -z "$OUT" ]; then
  echo "!! bridge 自检失败(exit=$RC),stdout 为空" >&2
  echo "   手工排查:$PY $ROOT/scripts/openclaw_bridge.py --message '【转发学生提问】Proteus 在哪下载?'" >&2
else
  echo "    bridge OK(exit=$RC),JSON 前 120 字:"
  printf '    %s\n' "${OUT:0:120}"
fi

# 【记录】这条通路单独自检。**故意用一条会被拒收的**(里面有假学号):
# 既能证明这条路走得通,又不会真往助教那份手写的 常问问题.txt 里写东西。
echo "==> [3/3] 【记录】通路自检(预期 type=record 且 ❌):"
set +e
OUT2="$("$PY" "$ROOT/scripts/openclaw_bridge.py" --message '【记录】问题:测试 20259999999 在哪交作业
标准答案:交给学委' 2>/dev/null)"
RC2=$?
set -e
case "$OUT2" in
  *'"type": "record"'*|*'"type":"record"'*) echo "    ✅ 通路正常,回执(被拒收是对的):${OUT2:0:100}" ;;
  *) echo "!! 【记录】没走到记录入口(exit=$RC2),实际输出:${OUT2:0:160}" >&2
     echo "   看 src/main.py 里那道闸门的顺序:【记录】必须在学生转发闸门**之前**判断。" >&2 ;;
esac

echo
if [ "$STALE" -eq 1 ]; then
  echo "!!! 注意:AGENTS.md 里的路由规则还是旧的(见上面的提醒),【记录】暂时不会生效。"
  echo
fi
echo "==> 完成。验证技能是否被 OpenClaw 看到:"
echo "    openclaw skills list | grep -i ta-assistant"
echo "==> 不经过微信、直接跑一轮 agent 测试:"
echo "    openclaw agent --agent main --message '【转发学生提问】什么是竞争冒险?'"
echo "    openclaw agent --agent main --message '【记录】问题:测试 20259999999 在哪交作业
标准答案:交给学委'   # 预期回一个 ❌,说明记录指令被路由到了后端"
