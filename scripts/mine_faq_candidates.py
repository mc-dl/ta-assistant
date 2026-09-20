#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从答疑日志里挖 FAQ 候选:学生**真问过**、但 FAQ 没接住的问法。

## 为什么要有它

FAQ 是手写的,写的时候只能靠"我猜学生会问什么"。而日志里躺着的是
**学生真问过什么** —— 每问一次就落一条 `qa_kind`(2026-09 一个月 265 条)。
在这个脚本之前,没有任何一条路径把日志变成 FAQ 条目,于是"库里没有的问法"
只能靠助教偶然撞见。这个脚本把那句话反过来:**先看真实问法,再决定补什么。**

## 它怎么判"没接住"

`qa_kind` 事件里已经落好了 `message`(问句原文)/ `kind`(分档)/
`faq_hit`(有没有命中 FAQ)/ `top_score`(资料检索最高分),于是:

| 日志里的样子 | 说明什么 |
|---|---|
| `faq_hit=False` 且问了很多次 | **FAQ 缺条目** ← 这个脚本的主产出 |
| `faq_hit=True` | FAQ 已经覆盖,别重复加 |
| `top_score` 很低 / 没有命中 | 资料库或锚点缺这块内容(另一种洞,先记下来) |

注:`top_score` 是 2026-09-20 才加进日志的字段,更早的日志里没有它。

## 同义改写怎么归并

按"实词集合"的 Jaccard 相似度,把每条问句跟**已有组的代表问法**比:
像就并进去,不像就自己开一组(阈值 `--similarity`,默认 0.5)。
实词的定义直接复用 FAQ 检索那边的 `_faq_content_tokens` —— 跟线上判定
"是不是命中"用的是同一套词,免得挖矿和线上对不上。

**先说清楚真实日志长什么样**(2026-09-20 量的,四个日志共 474 条问句):
去掉空白与标点后只有 **25 种不同写法**,而其中一条问题的**同一种写法被问了 158 次**
(占全日志三分之一)。也就是说这份日志的主要信息是"同一句话被反复问",
**不是**"同一件事有很多种说法"—— 归并这一层在这儿主要是把写法收拢,
而不是在做语义聚类。这直接决定了下面两件事:

**① 数的是"被问过多少次",不是"有几种写法"。** 这两个数在真实数据上差得很远
(158 vs 1)。先前数的是写法数,于是那条被问 158 次的问题因为"只有 1 种写法"
被 `--min-count 2` 判成"只问过一次"**整条丢掉** —— 最该补的那条恰恰是唯一被丢的。

**② 用"只跟代表问法比"而不是单链聚类。** 老实说,实测两者在现有日志上结果
几乎一样(阈值 0.5:单链 22 组 / 代表 23 组,最大组都是 158),所以这**不是**
一个"修好了某个bug"的改动。它防的是**语料变大之后**:单链只要 A~B、B~C 就并掉
A 和 C,哪怕两者毫不相干,而问句越短、公共实词(「电路」「作业」)越多,链走得越远。
现在最大的那组已经占全日志三分之一,再长下去单链会开始把不相干的问法吸进同一个团。
代表问法这一层没有链,放大时最坏是"分组偏细"—— 而偏细可以靠调小 `--similarity`
补回来,吸错了却分不开。

**"先按频次降序处理"有两个作用,别把它当成顺手排个序:**

1. 让**问得最多的那种说法先当上代表**,于是分组是围绕"学生实际最常用的说法"
   长出来的,而不是围绕某个只问过一次的怪写法;
2. 输出的代表问法取自组内问得最多的那种(`build_candidates` 里的 `most_common`),
   与第 1 条相互独立 —— 就算顺序写反了,代表问法也还是最多的那种,
   但分组的中心会漂到最冷门的写法上。

助教照着代表问法写 FAQ 条目,而 FAQ 是靠字面匹配命中的 ——
代表问法越接近学生的实际说法,下一次越容易被搜到。

**阈值方向仍然是保守的**:合并错了会把不相干的问题凑成一条 FAQ,而 FAQ 是
一条条写给学生看的,宁可分得细一点让人来合。输出里会把每组的**全部问法**
列出来,合错了当场看得见。

## 脱敏(必须)

日志存的是学生原文,可能带姓名/学号:

- 学号:8 位以上连续数字 → `[学号]`;
- 姓名:拿花名册(成绩记分册)里的姓名做替换 → `[学生]`;
- **微信昵称不在花名册里,认不出来** —— 所以导出的草稿在进 `常问问题.txt`
  之前**请自己过一眼**。

花名册读不到时只告警、跳过姓名脱敏(学号照遮),不会因此不干活。

## 用法

    python scripts/mine_faq_candidates.py                  # 挖 data/logs 下全部日志
    python scripts/mine_faq_candidates.py --min-count 1     # 连只问过一次的也列出来
    python scripts/mine_faq_candidates.py --kind admin      # 只看事务类
    python scripts/mine_faq_candidates.py --logs data/logs/2026-09.log
    python scripts/mine_faq_candidates.py --out draft.txt   # 导出可粘贴的 Q:/A: 草稿

草稿里 **`A:` 是空的**。这不是偷懒:FAQ 解析器要求 Q 和 A 都非空才算一条
(见 `retriever._parse_faq`),所以**半成品草稿直接粘进 `常问问题.txt`
不会污染线上 FAQ** —— 填好答案它才生效。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.rag.retriever import _faq_content_tokens  # noqa: E402
from src.utils.excel_ops import load_roster  # noqa: E402

_STUDENT_ID_RE = re.compile(r"\d{8,}")
# 只吃 `YYYY-MM.log`。同目录下还有 `llm.log`,那是**文本**日志不是事件 JSONL
# (每行 parse 不成功 → 全被丢掉),把它一起 glob 进来只会让"读入 N 行"这个数
# 虚高,看着像有数据其实一条都用不上。
_DEFAULT_LOG_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9].log"


@dataclass
class Ask:
    """一条答疑记录里挖出来的问句(文本已脱敏)。"""

    text: str
    kind: str
    faq_hit: bool
    top_score: float | None = None


def redact(text: str, names: list[str]) -> str:
    """遮掉学号与花名册里的姓名。

    先替姓名还是先遮学号无所谓,但两者都要做:有学生只发学号不发名字,
    也有只发名字的。
    """
    for name in names:
        if name and name in text:
            text = text.replace(name, "[学生]")
    return _STUDENT_ID_RE.sub("[学号]", text)


def signature(text: str) -> frozenset[str]:
    """问句的"实词集合",用来判两个问法是否在说同一件事。"""
    return frozenset(_faq_content_tokens(text))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """两个实词集合的相似度。任一为空则视为**不相似** —— 全是虚词的问句
    (「这个可以吗」)没有信息量,不该被并到任何一组里去。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster(asks: list[Ask], threshold: float) -> list[list[int]]:
    """按"代表问法"归并,返回每组的成员下标。

    **不是单链聚类** —— 理由见模块开头"为什么不是单链聚类"那段:
    单链在真实日志上会串出一个 150 条的巨团,巨团必含 FAQ 命中,
    于是永远挖不出候选。这里每条问句只跟**代表问法**比,链就断在代表这一层。

    **先按频次降序处理**,让问得最多的那种说法先当上代表 —— 分组的中心因此是
    学生的常用说法,而不是某个只问过一次的怪写法(输出里的代表问法另由
    `build_candidates` 的 `most_common` 决定,两件事互不替代)。
    同频次按文本排序,保证同一份日志跑两次归出来的组**逐字一样**。
    """
    freq = Counter(a.text for a in asks)
    order = sorted(range(len(asks)), key=lambda i: (-freq[asks[i].text], asks[i].text))

    reps: list[tuple[frozenset[str], list[int]]] = []
    for i in order:
        sig = signature(asks[i].text)
        for rep_sig, members in reps:
            if jaccard(sig, rep_sig) >= threshold:
                members.append(i)
                break
        else:
            reps.append((sig, [i]))
    return [members for _sig, members in reps]


def read_asks(paths: list[Path], names: list[str]) -> tuple[list[Ask], int]:
    """读日志里的 `qa_kind` 事件。返回 (问句列表, 读到的行数)。"""
    asks: list[Ask] = []
    lines_read = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            print(f"[跳过] 读不了 {path}:{exc}", file=sys.stderr)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            lines_read += 1
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue          # 半行(写到一半就断了)不该让整次挖掘失败
            if d.get("event") != "qa_kind":
                continue
            msg = (d.get("message") or "").strip()
            if not msg:
                continue
            asks.append(Ask(
                text=redact(msg, names),
                kind=d.get("kind") or "?",
                faq_hit=bool(d.get("faq_hit")),
                top_score=d.get("top_score"),
            ))
    return asks, lines_read


@dataclass
class Candidate:
    """一组问法。

    `n_asks`(被问过多少次)与 `phrasings`(有几种写法、各写了几次)是**两个数**,
    必须分开 —— 见下面 `build_candidates` 的注释,混起来会把最该看的整条丢掉。

    `phrasings` 按出现次数降序,第一条当代表问法。
    """

    phrasings: list[tuple[str, int]]
    kinds: Counter = field(default_factory=Counter)
    n_hit: int = 0
    n_asks: int = 0
    top_scores: list[float] = field(default_factory=list)

    @property
    def rep(self) -> str:
        return self.phrasings[0][0]

    @property
    def n_variants(self) -> int:
        return len(self.phrasings)

    @property
    def is_gap(self) -> bool:
        """一次都没命中 FAQ → 这是 FAQ 的缺口(而不是"已覆盖")。"""
        return self.n_hit == 0


def build_candidates(asks: list[Ask], groups: list[list[int]]) -> list[Candidate]:
    """把归好的组转成候选。

    **`n_asks` 数的是"被问过多少次",不是"有几种写法"。** 这两个数在真实日志上
    差得很远:2026-09-20 实测 474 条问句里只有 **25 种不同写法**,
    其中一条问题的**同一种写法被问了 158 次**(占全日志三分之一)。

    先前这里数的是"不同写法数"(`len(set(...))`),于是那条 158 次的问题因为
    "只有 1 种写法"被 `--min-count 2` 判为"只问过一次"而**整条丢掉** ——
    最该补的那条恰恰是唯一被丢掉的。测试也没抓住它,因为测试里的两条问句
    本来就是两种不同写法(写法数 == 次数,两个定义在那时候恰好相等)。
    """
    out: list[Candidate] = []
    for idxs in groups:
        members = [asks[i] for i in idxs]
        out.append(Candidate(
            phrasings=Counter(m.text for m in members).most_common(),
            kinds=Counter(m.kind for m in members),
            n_hit=sum(1 for m in members if m.faq_hit),
            n_asks=len(members),
            top_scores=[m.top_score for m in members if m.top_score is not None],
        ))
    # 问得多的在前;同频次按代表问法排序,保证**同一份日志跑两次结果一样**
    # (否则 dict/set 的顺序会让 diff 抖,没法拿输出做对比)
    out.sort(key=lambda c: (-c.n_asks, c.rep))
    return out


def render(cands: list[Candidate], threshold: float, n_asks: int, source: str,
           n_groups: int) -> str:
    """`n_groups` 是**过滤前**的组数。

    它必须单独传进来:先前这里打的是 `len(cands)`,而 cands 已经过
    min-count / 只看缺口两道筛 —— 于是一旦筛空,报告会写"聚成 0 组",
    看着像"这条日志没东西可挖",而真相是"聚出来了但都被筛掉了"。
    这两种情况该采取的动作完全不同(前者去查日志,后者去调阈值),
    所以数字不能混。
    """
    lines = [
        f"共读入 {n_asks} 条问句(来源:{source}),聚成 {n_groups} 组"
        f"(相似度阈值 {threshold})。",
        "",
    ]
    gaps = [c for c in cands if c.is_gap]
    lines.append(f"筛选后列出 {len(cands)} 组,其中 FAQ **一次都没接住**的 {len(gaps)} 组"
                 f" ← 这些才是要补的")
    if not cands:
        lines.append("(没有候选。若上面 n_groups 明显大于 0,说明是 min-count 太高"
                     "或该日志里每个相似问法组都至少命中过一次 FAQ —— "
                     "试试 --min-count 1 或 --all 看看被筛掉了什么)")
    lines.append("")
    for c in cands:
        kind = ",".join(f"{k}×{v}" for k, v in c.kinds.most_common())
        flag = "缺" if c.is_gap else "已有"
        low = ""
        if c.top_scores:
            low = f"  资料最高分 {min(c.top_scores):.2f}~{max(c.top_scores):.2f}"
        lines.append(
            f"[{flag}] 问过 {c.n_asks} 次({c.n_variants} 种写法)  {kind}  "
            f"{c.rep}{low}"
        )
        for text, n in c.phrasings[1:]:
            lines.append(f"            · {text}({n} 次)")
    return "\n".join(lines)


def render_draft(cands: list[Candidate]) -> str:
    """导出可粘贴的 FAQ 草稿:`A:` 留空,所以**粘进去也不会生效**。

    这样助教可以先把候选整批贴进 `常问问题.txt`,再一条条填答案;
    没填的那些不会被检索到,也不会以空答案的形式误答学生。

    **草稿里只放代表问法,且只放 `Q:` / `A:` 两种行。** 别顺手加 `#` 注释:
    `_parse_faq` 在 `A:` 之后遇到非空行会**把它并进答案里**
    (只有"空行+下一条 Q"或"下一条 Q"才收尾),于是一行注释会顶替空答案 ——
    草稿就从"惰性"变成"活着且答非所问"。同组的其他写法都印在屏上,
    要合并进 FAQ 的话由助教决定,不由脚本代笔。
    """
    blocks = [f"Q: {c.rep}\nA:" for c in cands]
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="从答疑日志挖 FAQ 候选")
    ap.add_argument("--logs", nargs="*", type=Path, default=None,
                    help="日志文件(默认 data/logs 下全部 *.log)")
    ap.add_argument("--min-count", type=int, default=2,
                    help="至少被问过几次才列出来(默认 2 —— 只问过一次的可能是"
                         "一个人打错字。注意数的是**被问次数**,同一种写法被问 100 次"
                         "也算 100)")
    ap.add_argument("--kind", default=None,
                    help="只看某一档:admin / concept / problem")
    ap.add_argument("--similarity", type=float, default=0.5,
                    help="实词 Jaccard 相似度阈值(默认 0.5;调小 = 并得更狠)")
    ap.add_argument("--all", action="store_true",
                    help="连 FAQ 已经接住的那几组也列出来(默认只看缺口)")
    ap.add_argument("--out", type=Path, default=None,
                    help="把候选导出成 Q:/A: 草稿(A 留空,填了才生效)")
    args = ap.parse_args()

    paths = args.logs if args.logs else sorted(config.LOGS_DIR.glob(_DEFAULT_LOG_GLOB))
    if not paths:
        print(f"没找到日志文件({config.LOGS_DIR}/{_DEFAULT_LOG_GLOB})", file=sys.stderr)
        return 1

    roster = load_roster()
    names = list(roster.values())
    if not names:
        print("[警告] 花名册读不到,只遮学号、**不遮姓名** —— "
              "导出前请自己再核一遍有没有学生姓名。", file=sys.stderr)

    asks, _ = read_asks(paths, names)
    if args.kind:
        asks = [a for a in asks if a.kind == args.kind]
    if not asks:
        print("日志里没有可用的问句(是不是还没跑过答疑?)", file=sys.stderr)
        return 1

    groups = cluster(asks, args.similarity)
    n_groups = len(groups)
    cands = build_candidates(asks, groups)
    cands = [c for c in cands if c.n_asks >= args.min_count]
    if not args.all:
        cands = [c for c in cands if c.is_gap]

    source = ", ".join(str(p) for p in paths)
    print(render(cands, args.similarity, len(asks), source, n_groups))

    if args.out:
        args.out.write_text(render_draft(cands), encoding="utf-8")
        print(f"\n草稿已写出:{args.out}(A 留空,填了才会生效)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
