#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索质量评测:一批「学生真会问的」提问,各自期望命中哪些资料。

**为什么要有这个脚本(而不是只在单测里断言)。**
`test/test_retrieval_recall.py` 用的是同一张表,但它只能回答"过/不过";
调参(改 `CHUNK_SIZE`、改锚点、加语料)时需要看到的是**分数和名次的移动**:
某个提问的第 3 名从 12.1 掉到 4.3、某条无关章节挤进了 top-7 —— 这些是"还能过,
但已经在退化"的信号。所以这个脚本把 top-K 连同分数一起打出来,并且能存基线、
跟基线比,把"悄悄变差"变成一条能看见的 diff。

用法:
    python scripts/eval_retrieval.py                    # 跑一遍,打表格
    python scripts/eval_retrieval.py --show-text        # 连命中的正文片段一起打
    python scripts/eval_retrieval.py -k 叠加            # 只跑名字含"叠加"的用例
    python scripts/eval_retrieval.py --save-baseline    # 存基线(默认 data/eval/retrieval_baseline.json)
    python scripts/eval_retrieval.py --compare          # 与基线比对,有回归则退出码 1
    python scripts/eval_retrieval.py --index 别的.pkl   # 拿别的索引跑(改语料前后对拍用)

    # 改语料/改锚点前后的标准动作(两条命令给出可 diff 的两份 JSON):
    #   python scripts/eval_retrieval.py --json before.json
    #   (改完重建索引)
    #   python scripts/eval_retrieval.py --json after.json
    #   python scripts/eval_retrieval.py --compare before.json

**期望值是量出来的,不是拍的。** 每条用例的 `must_hit` 都对着真实索引跑过;
新增用例时先跑一遍看实际命中什么,再决定它该期望什么 —— 期望"应该命中 A"
而实际命中 B 的时候,先怀疑期望写错了,其次是检索真的坏了,唯独不要改期望去
迁就现状(那样这张表就变成"把 bug 固化成规范"了)。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402

DEFAULT_BASELINE = config.PROJECT_ROOT / "data" / "eval" / "retrieval_baseline.json"

# 教材文件名 → 章号。`教材-第4章-电路定理.md`
_CHAPTER = re.compile(r"^教材-第(\d+)章-")


class Case(dict):
    """一条用例。用 dict 是为了能直接 json.dumps(基线比对要存下来)。

    字段:
      q            提问原文(照学生怎么说就怎么写,别改写成书面语)
      must_hit     期望出现在 top-K 里的文件名(命中**任意一个**就算过)
      must_hit_re  同上,但用正则(to 匹配"任意一个作业题文件"这种一类)
      must_not     不该出现的东西(比如目录类文件、别的课的资料)
      allow_ch     top-K 里允许出现的**教材章号**;不在这个集合里的教材章算噪声,
                    受 max_foreign 约束(见下)
      max_foreign  允许几条"无关教材章"。默认 0 —— 概念提问不该把无关章节拉进来
      note         这条用例守的是什么(给未来的自己看)
      kind         concept / problem / admin / other
    """

    def __init__(self, q, kind="concept", must_hit=(), must_hit_re=(), must_not=(),
                 allow_ch=(), max_foreign=0, note=""):
        super().__init__(q=q, kind=kind, must_hit=list(must_hit), must_hit_re=list(must_hit_re),
                         must_not=list(must_not), allow_ch=list(allow_ch),
                         max_foreign=max_foreign, note=note)


# ─── 用例表 ──────────────────────────────────────────────────────
# 期望值全部来自实测(`--show-text` 看实际召回,再决定期望什么)。
#
# **`max_foreign` 写的是"当前实测值",不是"理想值"。** 它是个**预算**:相等就过,
# 涨了就红 —— 作用是"不许变差",不是"现在没问题"。残留的跨章命中都有具体成因,
# 逐条写在 `note` 里;要改成 0 得先让检索真的做到,而不是先把数字改小。
CASES: list[Case] = [
    # ── 概念题:应当落到教材对应章 ──
    # 这条是用户亲手验收过的提问。注意它**同时**期望第4章(电路定理,主讲
    # 叠加)和第10章(正弦稳态,讲交流电路里的叠加应用)—— 第4章正文里"叠加原理"
    # 这个词本身不出现(书里叫"叠加定理"),学生说的"叠加原理"却更贴第10章的节标题。
    Case("什么是叠加原理", must_hit=("教材-第4章-电路定理.md",),
         allow_ch=(4, 10), max_foreign=2,
         note="用户验收过的提问。残留 2 条跨章命中:第17章讲傅里叶级数时用的'叠加'"
              "是谐波叠加(词同义不同),第5章那块是正文顺带提到 —— 词形匹配解决不了,"
              "记在这里当已知残留"),
    Case("叠加定理", must_hit=("教材-第4章-电路定理.md",), allow_ch=(4, 10),
         note="书里用的词。第10章讲它在交流电路里的应用,算相关章"),
    Case("戴维南定理怎么用", must_hit=("教材-第4章-电路定理.md",), allow_ch=(4, 10),
         note="定理名 + 口语化的'怎么用';第10章有交流电路里的戴维南等效"),
    Case("运算放大器", must_hit=("教材-第5章-运算放大器.md",), allow_ch=(5,)),
    Case("一阶电路的时间常数怎么求", must_hit=("教材-第7章-一阶电路.md",), allow_ch=(7,)),
    Case("正弦量的相量表示", must_hit=("教材-第9章-正弦量与相量.md",), allow_ch=(9,)),
    Case("三相电路", must_hit=("教材-第12章-三相电路.md",), allow_ch=(12, 13),
         note="第13章讲三相变压器,允许作为相关章节出现"),
    Case("拉普拉斯变换", must_hit=("教材-第15章-拉普拉斯变换简介.md",), allow_ch=(15, 16),
         note="第16章是它的应用"),
    Case("傅里叶级数", must_hit=("教材-第17章-傅里叶级数.md",), allow_ch=(17,)),
    Case("二端口网络", must_hit=("教材-第19章-二端口网络.md",), allow_ch=(19,)),
    Case("电路元件的相量关系", must_hit=("教材-第9章-正弦量与相量.md",), allow_ch=(9,),
         note="锚点词直接命中(学生可能照 PPT 标题问)"),

    # ── 数电:另一门课,不该被电路基础的教材挤掉 ──
    Case("什么是竞争冒险", kind="concept",
         must_hit_re=(r"实验讲义2024\.pdf$", r"数电.*\.docx$"),
         must_not=("错误集锦.md",),
         note="数电概念。两门课共用一份索引,不能用电路基础的东西答数电"),

    # ── 题号题:必须落到那个题号自己的文件 ──
    Case("1.36 怎么做", kind="problem", must_hit=("1.36.md",),
         note="题号提问;锚点复制进每一块就是为了这条"),
    Case("13.11 那题怎么做", kind="problem", must_hit=("13.11.md",)),
    Case("作业里的叠加定理怎么理解", kind="problem",
         must_hit_re=(r"^\d+\.\d+\.md$",), max_foreign=1,
         note="学生说的'作业里的',期望命中某个作业题文件。残留 1 条第7章命中"),
    Case("实验报告要写几页", kind="admin",
         must_hit=("实验报告模板（2026）.pdf",), max_foreign=1,
         note="残留 1 条教材第4章命中,靠的是'要/写'这类常用词 —— 词形匹配的固有残留"),

    # ── 事务/资料题 ──
    Case("教材上哪里有印错的地方", kind="admin", must_hit=("错误集锦.md",),
         allow_ch=(13,),
         note="错误集锦本身就是按书上页码写的(它引到第13章)"),
]


def _chapter_of(source: str) -> int | None:
    m = _CHAPTER.match(source)
    return int(m.group(1)) if m else None


def evaluate(retriever, case: Case, top_k: int) -> dict:
    hits = retriever.search(case["q"], top_k=top_k)
    sources = [h["source"] for h in hits]
    rows = [{"rank": i + 1, "score": round(h["score"], 2), "source": h["source"]}
            for i, h in enumerate(hits)]

    rank = None
    for i, s in enumerate(sources):
        if s in case["must_hit"] or any(re.search(p, s) for p in case["must_hit_re"]):
            rank = i + 1
            break

    foreign = []
    for s in sources:
        ch = _chapter_of(s)
        if ch is not None and case["allow_ch"] and ch not in case["allow_ch"]:
            foreign.append(s)
        elif ch is not None and not case["allow_ch"]:
            foreign.append(s)      # 没写 allow_ch = 不希望出现任何教材章

    violated = [s for s in sources if s in case["must_not"]]

    return {"q": case["q"], "kind": case["kind"], "rank": rank,
            "hit": rank is not None, "foreign": foreign, "violated": violated,
            "top": rows, "note": case["note"]}


def verdict(r: dict, case: Case) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if not r["hit"]:
        want = case["must_hit"] or case["must_hit_re"]
        problems.append(f"期望命中 {want} 却一条都没有")
    if r["violated"]:
        problems.append(f"出现了不该出现的资料 {r['violated']}")
    if len(r["foreign"]) > case["max_foreign"]:
        problems.append(f"无关教材章节 {len(r['foreign'])} 条 > 上限 {case['max_foreign']}:"
                        f"{r['foreign']}")
    return (not problems), problems


def main() -> int:
    ap = argparse.ArgumentParser(description="检索质量评测")
    ap.add_argument("--index", type=Path, default=None, help="索引文件(默认 config.BM25_INDEX_PATH)")
    ap.add_argument("-k", "--filter", default=None, help="只跑提问里含该子串的用例")
    ap.add_argument("--top-k", type=int, default=None, help="默认取 config.RAG_TOP_K")
    ap.add_argument("--show-text", action="store_true", help="打印命中的正文片段")
    ap.add_argument("--json", type=Path, default=None, help="把结果写成 JSON")
    ap.add_argument("--save-baseline", nargs="?", const=str(DEFAULT_BASELINE), default=None)
    ap.add_argument("--compare", nargs="?", const=str(DEFAULT_BASELINE), default=None,
                    help="跑完后与基线比对,有条目变差则退出码 1")
    args = ap.parse_args()

    from src.rag.retriever import BM25Retriever
    path = args.index or config.BM25_INDEX_PATH
    if not Path(path).exists():
        print(f"❌ 索引不存在:{path}\n   先跑:`python scripts/build_index.py`", file=sys.stderr)
        return 2
    r = BM25Retriever.load(Path(path))
    top_k = args.top_k or config.RAG_TOP_K
    cases = [c for c in CASES if not args.filter or args.filter in c["q"]]
    if not cases:
        print(f"❌ 没有用例匹配 {args.filter!r}", file=sys.stderr)
        return 2

    print(f"索引:{path}   片段 {len(r.chunks)} 条   top_k={top_k}   用例 {len(cases)} 条")
    print("=" * 78)
    results, n_bad = [], 0
    for case in cases:
        r_ = evaluate(r, case, top_k)
        ok, problems = verdict(r_, case)
        n_bad += not ok
        mark = "✅" if ok else "❌"
        rank = f"第{r_['rank']}名" if r_["hit"] else "未命中"
        print(f"{mark} {case['q']}   [{rank}]")
        for row in r_["top"]:
            flag = ""
            ch = _chapter_of(row["source"])
            if ch is not None and ch not in case["allow_ch"]:
                flag = "  ← 无关教材章"
            if row["source"] in case["must_not"]:
                flag = "  ← 不该出现"
            print(f"     {row['rank']}. {row['score']:7.2f}  {row['source']}{flag}")
            if args.show_text:
                txt = r.search(case["q"], top_k=top_k)[row["rank"] - 1]["text"]
                print(f"          {txt.replace(chr(10), ' ⏎ ')[:110]}")
        for p in problems:
            print(f"     ⚠️ {p}")
        print()
        results.append(r_)

    hits = [x for x in results if x["hit"]]
    rr = sum(1 / x["rank"] for x in hits) / len(results) if results else 0.0
    print("=" * 78)
    print(f"命中 {len(hits)}/{len(results)}   平均倒数名次(MRR) {rr:.3f}   "
          f"无关教材章共 {sum(len(x['foreign']) for x in results)} 条   "
          f"不达标用例 {n_bad} 条")

    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ 结果写入 {args.json}")
    if args.save_baseline:
        p = Path(args.save_baseline)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                                 "index_chunks": len(r.chunks), "top_k": top_k,
                                 "results": results}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        print(f"→ 基线已存 {p}")
    regressed = 0
    if args.compare:
        regressed = compare_with(Path(args.compare), results)
    return 1 if (n_bad or regressed) else 0


def compare_with(baseline: Path, now: list[dict]) -> int:
    """与基线逐条比对,**只报变化**(名次移动/噪声增减/命中丢失)。

    为什么不比总分:总分一样但两条用例的名次互换,恰恰是退化的样子 ——
    一条从第 2 掉到第 6、另一条从第 6 升到第 2,总分纹丝不动。
    """
    if not baseline.exists():
        # **返回 1,不是 0。** 2026-09-20 踩到:基线只存在于 WSL,
        # 一次 `rsync --delete` 把它删了,而 `--compare` 照样退出 0 ——
        # "闸门悄悄失效"比"没有闸门"更危险,因为人以为它在守着。
        print(f"❌ 基线不存在:{baseline}\n"
              f"   闸门没在起作用。先跑一次 `--save-baseline` 把当前(已核对过的)状态存下来;\n"
              f"   注意这个文件是**要随代码走**的(不在 .gitignore 里),"
              f"别让它只活在 WSL 一侧 —— Windows → WSL 的同步带 `--delete`,会把单边存在的文件删掉。",
              file=sys.stderr)
        return 1
    base = json.loads(baseline.read_text(encoding="utf-8"))
    old = {x["q"]: x for x in base["results"]}
    print("=" * 78)
    print(f"与基线比对:{baseline}({base.get('generated_at', '?')},"
          f"{base.get('index_chunks', '?')} 片段 → 现在 {len(now)} 条用例)")
    bad = 0
    for n in now:
        o = old.get(n["q"])
        if o is None:
            print(f"➕ {n['q']}:基线里没有(新加的用例)")
            continue
        diffs: list[str] = []
        if o["rank"] != n["rank"]:
            arrow = "↓变差" if (n["rank"] is None or (o["rank"] and n["rank"] > o["rank"])) else "↑变好"
            diffs.append(f"名次 {o['rank']} → {n['rank']} {arrow}")
        if len(n["foreign"]) > len(o["foreign"]):
            diffs.append(f"无关教材章 {len(o['foreign'])} → {len(n['foreign'])}"
                         f"(新增 {sorted(set(n['foreign']) - set(o['foreign']))})")
        if n["violated"] and not o["violated"]:
            diffs.append(f"新出现不该有的资料 {n['violated']}")
        if diffs:
            hard = any("变差" in d or "新出现" in d or "无关教材章" in d for d in diffs)
            bad += hard
            print(f"{'⚠️' if hard else '·'} {n['q']}: " + "; ".join(diffs))
    missing = [q for q in old if q not in {n["q"] for n in now}]
    if missing:
        print(f"➖ 基线里有、现在没跑的用例:{missing}(被删了?还是 --filter 过滤掉了?)")
    if not bad:
        print("✅ 没有条目变差。(名次上升/噪声减少也会列在上面,那些不算回归)")
    else:
        print(f"❌ {bad} 条变差 —— 若非有意为之,回滚这次改动;若是有意(比如换了语料),"
              f"跑 --save-baseline 把它定为新基线。")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
