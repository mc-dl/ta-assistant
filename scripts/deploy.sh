#!/usr/bin/env bash
# 一键部署脚本:从 Windows 同步代码 + 装依赖 + 建索引
# 在 WSL Ubuntu 里跑:
#   bash /mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant/scripts/deploy.sh
set -euo pipefail

SOURCE="/mnt/c/Users/YOUR_USERNAME/Downloads/ta-assistant"
TARGET="$HOME/ta-assistant"

echo "===> 1/4 从 Windows 同步代码..."
mkdir -p "$TARGET"
rsync -av --delete \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='data/logs' \
  --exclude='data/index' \
  --exclude='.env' \
  "$SOURCE/" "$TARGET/"

echo "===> 2/4 确保 .env 存在(首次才复制,不覆盖已有凭据)..."
if [ ! -f "$TARGET/.env" ]; then
  cp "$TARGET/.env.example" "$TARGET/.env"
  echo "    ⚠️ 新建了 .env,请记得填 MINMAX_API_KEY!"
fi

echo "===> 3/4 安装/更新依赖..."
bash "$TARGET/scripts/setup.sh"

echo "===> 4/4 构建知识库索引..."
cd "$TARGET"
# shellcheck disable=SC1091
source .venv/bin/activate
python scripts/build_index.py

echo ""
echo "============================================================"
echo "✅ 部署完成!测试一下:"
echo "   cd $TARGET && source .venv/bin/activate"
echo "   python -m src.main --message '【转发学生提问】Proteus 在哪下载?'"
echo "============================================================"
