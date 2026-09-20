#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事务题走 FAQ 通路时,大模型**转述**会不会改动 FAQ 原文里的数字。

**为什么要有这个脚本。** 文档和代码注释里曾经写着"事务题 + FAQ 命中 → 直接发答案,
不调大模型",但代码里从来没有那个 `return` —— 两边说的是两回事,而且**没人会发现**:
"直接发原文"和"润色一句"在回执上长得一模一样,只有对比过才知道差别。

那到底该补 `return`(真的不调)还是该改注释(承认要调)?**量过再定,不拍脑袋。**
这个脚本就是那把尺子:拿 FAQ 里每条**事务类**问题的原文当提问(保证命中自己),
走**真实** `qa.handle()` 通路,再用字符串比对"FAQ 答案里的数字"和"模型答复里的数字"。

- **FAQ 有、答复里没了** → 润色把信息弄丢了
- **答复里多出来了** → 润色自己编了东西
- 两者都没有 → 这一步**没有引入可观测的失真**

> ⚠️ 中文数字会先归一化:"第八周" vs "第8周"是同一个意思,不算改动。
> 但归一化的代价是**误报**:「一般」「一下」「乱七八糟」里的"一"会被当成数字 1。
> 所以看到"多了"先看上下文,别急着下结论 —— 第一轮实测的"多出来"全是这类误报。

用法(**会真的调 API**,所以不进单测):
    python scripts/eval_admin_fidelity.py              # 全部候选
    python scripts/eval_admin_fidelity.py --limit 6    # 只跑前 6 条(省钱、冒烟)
    python scripts/eval_admin_fidelity.py --show-extra # 连"多出来"的数字所在的片段一起打
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.handlers import qa  # noqa: E402
from src.llm.minmax import MinMaxClient  # noqa: E402
from src.rag.retriever import FAQRetriever  # noqa: E402

# 中文数字也要归一化:"第八周" vs "第8周" 是同一个意思,不能算改动
_CN_DIGIT = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
             "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
             "两": "2", "半": "0.5"}


def digits(text: str) -> set[str]:
    """抽出文本里的数字(阿拉伯数字 + 中文数字),用于粗粒度一致性比对。"""
    nums = set(re.findall(r"\d+", text))
    nums |= {_CN_DIGIT[c] for c in text if c in _CN_DIGIT}
    return nums


class NoRAG:
    """admin + FAQ 命中这条路**不应该**碰 RAG。碰了就炸出来。

    这是本脚本里唯一一条"断言代码行为"的部分 —— 其余都是在看模型的输出。
    """

    def search(self, q, top_k=5):
        raise AssertionError("admin + FAQ 命中时不应该查资料!")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 条(0 = 全部;冒烟时用 6)")
    ap.add_argument("--show-extra", action="store_true",
                    help="把'多出来'的数字所在的片段也打出来(判断是不是中文数字误报)")
    args = ap.parse_args()

    faq = FAQRetriever.from_default()
    llm = MinMaxClient.from_env()
    print(f"FAQ 条数={len(faq.qa_pairs)}  LLM 可用={llm.available()}  "
          f"FAQ_HIT_THRESHOLD={config.FAQ_HIT_THRESHOLD}")
    if not llm.available():
        print("LLM 不可用(没配 key 或端点不通),这个脚本跑了也没有意义。", file=sys.stderr)
        return 2

    # 候选 = 判成事务档、且拿自己的问题原文当提问时能命中自己的那些条目
    cands = []
    for q, a in faq.qa_pairs:
        if qa._classify(q) != "admin":
            continue
        hits = faq.search(q, top_k=1)
        if hits and hits[0]["score"] >= config.FAQ_HIT_THRESHOLD:
            cands.append((q, a))
    print(f"事务类且能命中自己的 FAQ 条数={len(cands)}")
    if args.limit:
        cands = cands[:args.limit]
        print(f"(--limit {args.limit},实际跑 {len(cands)} 条)")

    ok = lost_n = add_n = err_n = 0
    lost_rows: list[int] = []
    add_rows: list[int] = []
    for i, (q, a) in enumerate(cands):
        try:
            out = qa.handle(q, faq_retriever=faq, rag_retriever=NoRAG(), llm=llm)
        except Exception as e:  # noqa: BLE001
            err_n += 1
            print(f"  [{i}] ERROR  {type(e).__name__}: {e}")
            continue
        reply = out["reply"]
        d_faq, d_rep = digits(a), digits(reply)
        missing = d_faq - d_rep          # FAQ 有、答复里没了 → 润色把信息弄丢了
        extra = d_rep - d_faq            # 答复里多出来的 → 润色自己加了东西
        if missing:
            lost_n += 1
            lost_rows.append(i)
        if extra:
            add_n += 1
            add_rows.append(i)
        if not missing and not extra:
            ok += 1
        # 刻意**不打 FAQ 正文**:那里面可能有不该抄进日志的课程事务原文。
        # 只在有差异时打差异数字本身,不打断句子。
        tag = ("MATCH" if not missing and not extra
               else ("LOST " if missing else "EXTRA"))
        print(f"  [{i}] {tag}  FAQ数字={sorted(d_faq)}  答复数字={sorted(d_rep)}"
              + (f"  丢了={sorted(missing)}" if missing else "")
              + (f"  多了={sorted(extra)}" if extra else ""))
        if args.show_extra and extra:
            # 找出这些数字在答复里的上下文(前后各 8 字),用来判断
            # 是不是「一下」「一份」这类中文数字误报。先把换行压平再找,
            # 否则跨行的片段会打成空的 `……`。
            flat = re.sub(r"\s+", " ", reply)
            for n in sorted(extra):
                pat = re.escape(n) if n.isdigit() and len(n) > 1 \
                    else f"[{n}一二三四五六七八九十两半]"
                for m in re.finditer(pat, flat):
                    s = max(0, m.start() - 8)
                    print(f"        …{flat[s:m.end() + 8]}…")

    print(f"\n汇总(共 {ok + lost_n + add_n} 条,另有 {err_n} 条报错):")
    print(f"  ✅ 完全一致(数字集合一模一样):{ok} 条")
    print(f"  ⚠️  **丢了** FAQ 原文里的数字      :{lost_n} 条  行号={lost_rows}")
    print(f"  ℹ️  答复里**多出**数字            :{add_n} 条  行号={add_rows}")
    print("\n'丢了'才是失真 —— 它要能归零,否则这条快路就不该保留润色。")
    print("'多了'要人眼看上下文:实测绝大多数是中文数字当字用(一下/一份/一般)、")
    print("Markdown 序号(1. 2. 3.)、或模型自己补的常识(A4 纸),不是 FAQ 数字被改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
