#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""花名册自检:一次跑完,把"能不能用"摊开说清楚。

    .venv/bin/python scripts/check_roster.py
    .venv/bin/python scripts/check_roster.py --path /some/other.xlsx

**为什么专门写这个脚本。**
`load_roster()` 出过的两个 bug 有一个共同形状:**返回空字典,而不是报错**。
  · 路径写成 `WINDOWS_ROOT/"数电资料"/…`,那个目录根本不存在 → 空字典;
  · `read_only=True` 遇上没有 `<dimension>` 的 xlsx → 被读成 1 行 1 列 → 空字典(不报错)。
两种情况下补交登记都**照常工作**,只是校验状态永远写「名单缺失」——
功能停摆了好几个月,没有任何一处会红。所以这里补一次**正向自检**:
"名单现在是好的"这件事必须能被一条命令证明出来。

退出码:0 = 名单可用;1 = 读不出来 / 一个人都没有(检查没做成要当失败报)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.utils.excel_ops import scan_roster  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="花名册自检")
    ap.add_argument("--path", default=None, help="默认用 config.GRADEBOOK_PATH")
    args = ap.parse_args()

    path = Path(args.path) if args.path else config.GRADEBOOK_PATH
    scan = scan_roster(path)

    print(f"花名册路径 : {scan.path}")
    print(f"配置来源   : config.GRADEBOOK_PATH"
          f"(可用环境变量 ROSTER_PATH 覆盖)")

    if not scan.ok:
        print(f"\n❌ 读不出来:{scan.reason}")
        print("\n补交登记的「学号/姓名校验」会退化成「名单缺失」——"
              "登记不会失败,所以**不看这行就发现不了**。")
        return 1

    print(f"表头       : 第 {scan.header_row} 行"
          f"(学号在第 {scan.id_col} 列、姓名在第 {scan.name_col} 列,按表头文字自动定位)")
    print(f"学生人数   : {scan.count}")
    print(f"学号位数   : {dict(sorted(scan.id_lengths.items()))}")
    if scan.skipped_rows or scan.duplicates:
        print(f"跳过的行   : {scan.skipped_rows}"
              f"(学号或姓名不合规) / 同学号异名 {scan.duplicates} 条(保留先出现的)")

    if scan.count == 0:
        print("\n❌ 文件打开了、表头也找到了,但一个学生都没解析出来。"
              "多半是学号列不是纯数字,或者表头文字不在 config.ROSTER_ID_HEADERS 里。")
        return 1

    # 学号位数和提交消息里那条正则(8 位)对不上时,名单再对也永远匹配不上 ——
    # 这类"两边各自都对、拼在一起不对"的问题,只有在这里能提前看见。
    if set(scan.id_lengths) != {8}:
        print(f"\n⚠️  学号位数不是**清一色 8 位**。"
              f"提交消息侧的正则只认 8 位连续数字(src/handlers/submission.py "
              f"的 STUDENT_ID_RE),不匹配的学号会一律判成「学号不在名单」。")

    print("\n✅ 名单可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
