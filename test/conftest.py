"""pytest 配置:让 `pytest` 能找到 src 包。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
