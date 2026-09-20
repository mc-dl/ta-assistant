#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字保留审计:大模型改写后的中文解析里,原文的每个数字是否都还在?

为什么要单独有这个脚本:
    `enhance_circuit_basic.py` 让大模型把英文解答改写成中文详细解析,
    **唯一真正危险的地方就是它悄悄改了一个数** —— "I = 20/0.25 = 80 A" 里
    80 变成 8,学生照着做作业就错,而且中文读起来毫无破绽。
    所以每次增强完都要跑一遍这个审计。

怎么判断:
    把原文里所有"有意义的数字"(≥3 位,或带小数点)抠出来,逐个查中文解析里有没有。
    缺失的不一定就是错,但**必须人工看一眼**。按经验,命中率高的原因是这几类:
      - 别题的题号(图注 "For Prob. 11.12")
      - PDF 丢了上标:10³ 被提取成 "103"、8.883×10⁹ 成了 "8.883x109"
      - PDF 把分数/乘号拍平:70∥30 成了 "7030"、20∥5 成了 "205"
      - 纯精度差异:原文 13.51342 → 解析写 13.513(没错)
      - 原文是 MATLAB 输出表格,解析改成了概括叙述

用法:
    python scripts/audit_enhanced_numbers.py                  # 只报告
    python scripts/audit_enhanced_numbers.py --context        # 连原文上下文一起打出来
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402

MARK_CN = "【中文详细解析】"
MARK_SRC = "【原文(核对用,未经改动)】"
# 中文版题的原文后面还跟着「英文版对照」段。**审计必须在这里截断** ——
# 国际版个别题参数/单位本就与中文版不同(比如公里↔英里),把英文那段算进"原文",
# 就会把"中文解析里没有英文版独有的那个数"报成缺失,把最该看的那几行淹掉。
MARK_EN_REF = "【英文版对照(仅参考)】"

# 讲义(张老师课件)和错误集锦本来就**不该有**大模型写的解析,不参与审计。
_NON_PROBLEM = ("错误集锦.md",)

# 这些数字出现在原文里但不算"缺失":
#   - 年份(2017 之类)
#   - 图号 / 文献号(X.YYY 形式)
_YEAR = re.compile(r"(?:19|20)\d\d")
_FIGNO = re.compile(r"\d{1,2}\.\d{3}")


def _meaningful_numbers(text: str) -> set[str]:
    nums = {n for n in re.findall(r"\d+(?:\.\d+)?", text)
            if len(n) >= 3 or "." in n}
    return {n for n in nums if not _YEAR.fullmatch(n) and not _FIGNO.fullmatch(n)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=config.MATERIALS_DIR / "电路基础")
    ap.add_argument("--context", action="store_true", help="打印原文上下文")
    args = ap.parse_args()

    files = [p for p in args.dir.rglob("*.md")
             if not p.name.startswith(("00_", "_"))
             and "讲义" not in p.relative_to(args.dir).parts
             and p.name not in _NON_PROBLEM]
    if not files:
        print(f"没找到语料:{args.dir}")
        return 1

    tot = 0
    no_mark: list[str] = []
    suspicious: list[tuple[str, dict[str, str]]] = []

    for p in sorted(files):
        t = p.read_text(encoding="utf-8")
        if MARK_CN not in t or MARK_SRC not in t:
            no_mark.append(str(p.relative_to(args.dir)))
            continue
        cn = t.split(MARK_CN, 1)[1].split(MARK_SRC, 1)[0]
        # 中文版题只审**中文原文**:截到「英文版对照」为止(理由见 MARK_EN_REF 注释)
        sr = t.split(MARK_SRC, 1)[1].split(MARK_EN_REF, 1)[0]
        # 题号本身会出现在原文(如 "Solution 1.36")但解析里不必重复,不算缺失;
        # "103" 是 PDF 把上标 10³ 提取丢了的产物,也不是解析的问题。
        drop = {p.stem, "103"}
        miss = sorted(n for n in _meaningful_numbers(sr) if n not in cn and n not in drop)
        tot += 1
        if miss:
            ctx = {}
            for n in miss:
                i = sr.find(n)
                ctx[n] = re.sub(r"\s+", " ", sr[max(0, i - 55):i + 25]).strip()
            suspicious.append((str(p.relative_to(args.dir)), ctx))

    print(f"审计 {tot} 份带中文解析的语料")
    print(f"  数字全部保留:{tot - len(suspicious)} 份")
    print(f"  有数字未出现:{len(suspicious)} 份  ← 逐条人工确认,常见原因是 PDF 提取产物")
    if no_mark:
        print(f"  ⚠️ 没有解析标记(增强失败?):{len(no_mark)} 份 {no_mark[:5]}")
    print()
    for f, ctx in suspicious:
        print(f"── {f}")
        for n, c in ctx.items():
            if args.context:
                print(f"     {n:12s} 原文上下文: …{c}…")
            else:
                print(f"     {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
