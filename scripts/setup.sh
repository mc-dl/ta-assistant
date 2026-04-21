#!/usr/bin/env bash
# 一键安装依赖(在 WSL Ubuntu 上运行)
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "===> 项目根目录: $ROOT"

# 1. 系统依赖(libreoffice 转 .doc,poppler 协助 pdf)
echo "===> 安装系统依赖(需要 sudo)"
if ! command -v libreoffice >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y libreoffice-core libreoffice-writer poppler-utils python3-venv python3-pip
else
    echo "    libreoffice 已安装,跳过"
fi

# 2. 虚拟环境(已存在则跳过,加速重复部署)
if [ ! -d "$ROOT/.venv" ]; then
    echo "===> 创建虚拟环境 .venv"
    python3 -m venv "$ROOT/.venv"
else
    echo "===> .venv 已存在,跳过重建(直接安装依赖)"
fi

# 3. Python 依赖
echo "===> 安装 Python 依赖"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"

# 4. 预热 jieba(首次用会下载词典)
echo "===> 预热 jieba 分词"
python -c "import jieba; list(jieba.cut('预热分词器'))"

# 5. 复制 .env 模板
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "===> 已创建 .env,请编辑填入 MinMax 的 API Key"
fi

# 6. 测试导入
echo "===> 自检"
python -c "from src.main import process; print('✅ 模块导入成功')"

echo ""
echo "===================================================================="
echo "✅ 安装完成!下一步:"
echo "   1. 编辑 .env,填入 MINMAX_API_KEY / MINMAX_GROUP_ID"
echo "   2. 构建索引:python scripts/build_index.py"
echo "   3. 测试:python -m src.main --message '【转发学生提问】我作业漏交了 XXX 20230001 第1次'"
echo "===================================================================="
