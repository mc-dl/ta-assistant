#!/usr/bin/env python3
"""一次性:扫描数电资料,构建 BM25 索引。资料更新后重跑即可。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from src.rag.indexer import build_index
    print("=" * 60)
    print("构建数电资料 BM25 索引")
    print("=" * 60)
    try:
        n = build_index(verbose=True)
        print(f"\n共生成 {n} 个片段。")
        return 0
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        print("请检查 src/config.py 里的 MATERIALS_DIR 是否正确。")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ 构建失败:{e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
