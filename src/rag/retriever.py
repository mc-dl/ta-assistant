"""检索器:BM25(数电资料)和 FAQRetriever(常问问题.txt)。"""
from __future__ import annotations

import math
import pickle
import re
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from src import config
from src.utils.stderr_log import warn as _warn


# ─── FAQ 通路的"实词重叠闸门" ────────────────────────────────
# 要解决的问题:FAQ 是"短问题 vs 短问题"的匹配,中文疑问句里 是/的/什么/时候
# 这些词几乎每句都有。实测「什么是竞争冒险」会以 4.83 分假命中
# 「电路基础作业的截止时间是什么时候?」(阈值 4.0),于是把"截止时间"的答案发给
# 一个问概念的学生。2026-09-20 踩到。
#
# 为什么不是"把停用词从打分里删掉":那样会让**真实命中**的分数一起掉下来——
# 实测「这周电路基础有作业吗」5.11 → 1.88、「Proteus 在哪下载」5.48 → 3.03,
# 4.0 这个阈值是按原分词标定的,动了分词就等于动了阈值。
#
# 所以这里只把停用词当**闸门**用,不打分:
#   打分照旧(原 jieba 分词,阈值含义不变),但要求命中的那条 FAQ
#   **至少共享一个实词**;一个都不共享就当作没命中,退回落 RAG。
# 实测效果:假命中归零,原来的真命中分数一分不掉。
_FAQ_STOPWORDS = frozenset("""
的 了 是 在 我 你 他 她 它 我们 你们 他们 咱们 有 和 与 或 及 就 都 也 还 很 太 不 没 没有 别
要 会 能 可以 能否 是否 应该 需要 吗 呢 吧 啊 呀 哦 嗯 请 请问
一下 一个 一些 一点 这个 那个 这些 那些 哪个 哪些 这里 那里 哪里 哪儿
什么 怎么 怎样 如何 为什么 时候 什么时候 的话 以及 并且 但是 因为 所以 如果 就是 还有
关于 对于 通过 进行 比如 例如
这 那 这题 那题 这道 那道 这条 那条 这种 那种
""".split())


def _faq_content_tokens(text: str) -> set[str]:
    """FAQ 闸门用:只留实词(去掉虚词与疑问词)。"""
    return {t for t in (x.strip() for x in jieba.cut(text))
            if t and t not in _FAQ_STOPWORDS}


class BM25Retriever:
    """基于 pickle 索引的 BM25 检索。"""

    def __init__(self, bm25: BM25Okapi, chunks: list[dict]):
        self.bm25 = bm25
        self.chunks = chunks

    @classmethod
    def load(cls, path: Path | None = None) -> "BM25Retriever":
        path = path or config.BM25_INDEX_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"索引文件不存在:{path}。请先运行 `python scripts/build_index.py`"
            )
        with path.open("rb") as f:
            data = pickle.load(f)
        # 装载时也修一遍 IDF:老索引(pickle 里存的是旧的 epsilon 版 IDF)不该因为
        # "没重建"就继续带着虚词噪声 —— 见 `apply_lucene_idf`。
        apply_lucene_idf(data["bm25"])
        return cls(bm25=data["bm25"], chunks=data["chunks"])

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not query.strip():
            return []
        tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokens)
        idxs = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
        return [
            {
                **self.chunks[i],
                "score": float(scores[i]),
            }
            for i in idxs
            if scores[i] > 0
        ]


def apply_lucene_idf(bm25: BM25Okapi) -> int:
    """把 BM25 的 IDF 换成 Lucene 版,**就地**改,返回改了几个词。

    **为什么非改不可(2026-09-20 实测)。**
    `rank_bm25.BM25Okapi` 算的是 `idf = log(N - df + 0.5) - log(df + 0.5)`,
    出现在半数以上分块里的词会算成**负数**,而它对负数的处理是**统一兜底**成
    `epsilon × average_idf`(epsilon=0.25)。实测本库:
        `的` df=2959/3166、`是` df=2453、`原理` df=2401、`怎么` df=2595 …
        这些词的 idf 全部等于 **1.6994** —— 同一个正数,且与 `叠加`(真·关键词,
        idf 4.40)只差 2.6 倍。
    后果是**虚词变成了正证据**:问「什么是叠加原理」,`什么`+`是`+`原理`三个词
    给每一章都白送 7.3 分(实测 教材-第5章-运算放大器.md 那一块 13.53 分里有 7.3
    分来自这三个词),于是"运算放大器"和"傅里叶级数"两章挤进 top-7,
    而真正该讲的第 4 章被压到第 4 名。
    锚点里的通用词尾巴(`定义 概念 原理 定理 公式 推导 讲解 详解 怎么理解 为什么`)
    把这个效应放大了 —— 它们被复制进**每一个**教材分块,等于给全部 19 章
    同时发了一张"什么都能答"的通行证。

    改法用 Lucene 的写法:`idf = log(1 + (N - df + 0.5) / (df + 0.5))`。
    它单调递减、**恒为正、且不兜底**:
        df=38(叠加)    → 4.41(原来是 4.40,几乎不变)
        df=225(叠加定理)→ 2.64(原来是 2.57,略升)
        df=2401(原理)  → 0.28(原来是 1.70,**噪声降到 1/6**)
        df=2959(的)    → 0.09(原来是 1.70)
    也就是**稀有词不被削、常见词被压平**,这正是 IDF 该干的事。
    没有采用"直接清零":那会让「问句只由虚词组成」时全库 0 分、一条都召不回;
    Lucene 版留了很小的一点权重,排序意义不变而噪声基本消失。

    **只用于资料检索(`BM25Retriever`)。** `FAQRetriever` 用的是另一个
    BM25 实例、另一套逻辑:它的 4.0 阈值就是按旧 IDF 标定的,换公式等于偷偷改了
    阈值(实测「这周电路基础有作业吗」5.11 → 1.88),那边靠"实词闸门"解决同一个
    问题,见 `_FAQ_STOPWORDS` 上方注释。
    """
    n = len(bm25.doc_freqs)
    if not n:
        return 0
    df: dict[str, int] = {}
    for doc in bm25.doc_freqs:
        for w in doc:
            df[w] = df.get(w, 0) + 1
    changed = 0
    for w, d in df.items():
        new = math.log(1.0 + (n - d + 0.5) / (d + 0.5))
        if bm25.idf.get(w) != new:
            bm25.idf[w] = new
            changed += 1
    return changed


class FAQRetriever:
    """FAQ 检索:吃常问问题.txt,每次启动现建 BM25(数据量小,很快)。"""

    def __init__(self, qa_pairs: list[tuple[str, str]]):
        self.qa_pairs = qa_pairs
        if qa_pairs:
            # 打分用原分词(保持阈值 4.0 的含义不变)
            self.bm25 = BM25Okapi([list(jieba.cut(q)) for q, _ in qa_pairs])
            # 闸门用实词集合
            self.content_tokens = [_faq_content_tokens(q) for q, _ in qa_pairs]
        else:
            self.bm25 = None
            self.content_tokens = []

    @classmethod
    def from_default(cls) -> "FAQRetriever":
        return cls.from_file(config.FAQ_PATH)

    @classmethod
    def from_file(cls, path: Path) -> "FAQRetriever":
        if not path.exists():
            _warn(f"[FAQRetriever] FAQ 文件不存在:{path}")
            return cls([])
        text = path.read_text(encoding="utf-8", errors="ignore")
        return cls(_parse_faq(text))

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        if not query.strip() or not self.bm25 or not self.qa_pairs:
            return []
        scores = self.bm25.get_scores(list(jieba.cut(query)))
        q_content = _faq_content_tokens(query)
        idxs = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
        out: list[dict] = []
        for i in idxs:
            if scores[i] <= 0:
                continue
            # 闸门:必须共享至少一个实词,否则就是被"是什么/怎么/的"这类词凑出来的假命中
            if not (self.content_tokens[i] & q_content):
                continue
            out.append({
                "question": self.qa_pairs[i][0],
                "answer": self.qa_pairs[i][1],
                "score": float(scores[i]),
            })
        return out


_Q_PREFIX = re.compile(r"^\s*Q\s*[:：]\s*", re.IGNORECASE)
_A_PREFIX = re.compile(r"^\s*A\s*[:：]\s*", re.IGNORECASE)


def _parse_faq(text: str) -> list[tuple[str, str]]:
    """解析 `Q:\n...\nA:\n...\n\n` 格式。Q/A 可以多行,空行分隔条目。

    **切分条目一律用 `_Q_PREFIX` / `_A_PREFIX` 匹配,不要写 `startswith("Q:")`。**
    2026-09-20 实测的坑:原来收集答案那段写的是
        `if nxt.startswith("Q:") or nxt.startswith("Q:"):`
    —— 同一个表达式写了两遍(显然是想写第二个变体却复制错了),而 `_Q_PREFIX`
    里的全角 `：` 恰恰就是漏掉的那个变体。后果:
      · 整份用全角写的 FAQ(两条)会被解析成**一条**;
      · 半角 `Q:` 后面跟全角 `Q：` 时,后一条的**问题会被吞进前一条的答案**里,
        而没有任何报错 —— 静默出错,最难查。
    现有的 `常问问题.txt` 是纯半角(实测 Q:/A: 各 37 个),所以今天没炸;
    但助教在中文输入法下编辑那个文件,打出 `：` 是迟早的事。
    """
    pairs: list[tuple[str, str]] = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _Q_PREFIX.match(line):
            # 收集 Q
            q_lines = [_Q_PREFIX.sub("", line)]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if _A_PREFIX.match(nxt):
                    break
                if nxt == "":
                    break
                q_lines.append(nxt)
                i += 1

            # 收集 A
            a_lines: list[str] = []
            if i < len(lines):
                cur = lines[i].strip()
                if _A_PREFIX.match(cur):
                    a_lines.append(_A_PREFIX.sub("", cur))
                    i += 1
                    while i < len(lines):
                        nxt = lines[i].strip()
                        # 空行 + 下一条 Q 结束当前条目
                        if nxt == "" and (
                            i + 1 < len(lines)
                            and _Q_PREFIX.match(lines[i + 1].strip())
                        ):
                            break
                        if _Q_PREFIX.match(nxt):
                            break
                        a_lines.append(lines[i])
                        i += 1

            q = "\n".join(q_lines).strip()
            a = "\n".join(a_lines).strip()
            if q and a:
                pairs.append((q, a))
        else:
            i += 1
    return pairs
