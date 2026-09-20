# -*- coding: utf-8 -*-
"""BM25 的 IDF:修掉"虚词被当成正证据"那个兜底(2026-09-20)。

`rank_bm25.BM25Okapi` 对**出现在半数以上文档里**的词,IDF 算出来是负的,
它一律兜底成 `epsilon × average_idf`(epsilon=0.25)。实测本库 `的`(df 2959/3166)、
`是`(2453)、`原理`(2401)、`怎么`(2595)全部等于 **1.6994** —— 同一个正数,
只比真关键词 `叠加`(4.40)小 2.6 倍。于是"什么词都匹配"反倒成了证据:
`什么是叠加原理` 这句话里 `什么`+`是`+`原理` 给**每一章**白送 7.3 分,
把无关章节顶到了正确章节前面。

`apply_lucene_idf` 换成 Lucene 的写法 `log(1 + (N-df+0.5)/(df+0.5))`:
恒为正、单调递减、不兜底。这个文件钉住"它确实这么算",以及
"**不许把它用到 FAQ 上**"(FAQ 的 4.0 阈值是按旧 IDF 标定的)。
"""
from __future__ import annotations

import math

import jieba
import pytest
from rank_bm25 import BM25Okapi

from src.rag.retriever import FAQRetriever, apply_lucene_idf


def _bm25(docs: list[list[str]]) -> BM25Okapi:
    return BM25Okapi([list(d) for d in docs])


def _mixed_corpus() -> list[list[str]]:
    """一个**像真实语料**的小库:几种常见度不同的虚词 + 一批各不相同的稀有词。

    搭成这样是**为了让"常见度不同"这件事可测**:`的` 出现在 20 篇里、`是` 16 篇、
    `原理` 12 篇 —— 真实语料里它们的 df 也是各不相同(`的` 2959、`是` 2453、
    `原理` 2401),而**兜底会把它们抹成同一个数**。这才是要复现的病。

    另一半是稀有词(各 1 篇):兜底值 = `epsilon × average_idf`,而 `average_idf`
    是**全库** IDF 的均值 —— 稀有词不够多,均值就是负的,兜底值也跟着为负,
    分数会被 `scores[i] > 0` 先滤掉,症状完全不同(这也是单条语料的 FAQ 测试
    一直是假绿的原因)。
    """
    docs = [["的", "是", "原理"] for _ in range(12)]
    docs += [["的", "是", f"稀有词A{i}"] for i in range(4)]
    docs += [["的", f"稀有词B{i}"] for i in range(4)]
    return docs


# ─── 兜底问题的本体 ─────────────────────────────────────────────
def test_floored_idf_is_flat_regardless_of_how_common_a_word_is():
    """**先证明病真的存在**:常见度差 8 篇的三个词,拿到的是同一个权重。

    这就是"虚词被当成正证据"的根:`的`(df 20/20)和 `原理`(df 12/20)权重相等,
    于是"什么词都匹配"与"确实匹配了一点"在打分上**分不出来**。
    """
    bm25 = _bm25(_mixed_corpus())
    vals = {t: bm25.idf[t] for t in ("的", "是", "原理")}
    assert len(set(vals.values())) == 1, f"前提变了:常见词的 IDF 不再相同 —— {vals}"
    assert next(iter(vals.values())) > 0, \
        "前提变了:兜底值不再是正数(虚词不再被当成正证据)—— 这条修法该重新评估"


def test_lucene_idf_separates_common_words_by_how_common_they_are():
    """修完之后:常见词之间也要**按 df 拉开**,而不是糊成一团。"""
    bm25 = _bm25(_mixed_corpus())
    apply_lucene_idf(bm25)
    assert bm25.idf["的"] < bm25.idf["是"] < bm25.idf["原理"], \
        (f"常见词还是没拉开:的={bm25.idf['的']:.3f} 是={bm25.idf['是']:.3f} "
         f"原理={bm25.idf['原理']:.3f}")
    assert bm25.idf["的"] > 0, "不该清零 —— 清零会让'问句只有虚词'时全库一条都召不回"


def test_lucene_idf_is_monotone_in_document_frequency():
    """权重必须随 df 单调递减:这是 IDF 的定义,也是最该守住的性质。"""
    docs = [["w"]] + [["x"]] * 4 + [["y"]] * 9
    bm25 = _bm25(docs)
    apply_lucene_idf(bm25)
    assert bm25.idf["w"] > bm25.idf["x"] > bm25.idf["y"] > 0


def test_lucene_idf_matches_the_formula():
    """照公式逐项核对一遍,防止以后有人"顺手优化"成别的写法。"""
    n, df = 10, 3
    docs = [["t"]] * df + [["other"]] * (n - df)
    bm25 = _bm25(docs)
    apply_lucene_idf(bm25)
    assert bm25.idf["t"] == pytest.approx(math.log(1 + (n - df + 0.5) / (df + 0.5)))


def test_lucene_idf_is_idempotent():
    """索引装载时会再修一遍 —— 第二次的结果必须与第一次一致(否则每次装载分数都在漂)。"""
    bm25 = _bm25(_mixed_corpus())
    apply_lucene_idf(bm25)
    snapshot = dict(bm25.idf)
    apply_lucene_idf(bm25)
    assert bm25.idf == snapshot
    assert apply_lucene_idf(bm25) == 0, "第二遍不该还有词需要改"


def test_lucene_idf_survives_an_empty_index():
    """空语料(还没建索引)不能把装载流程搞崩。"""
    bm25 = BM25Okapi([["占位"]])
    bm25.doc_freqs = []
    assert apply_lucene_idf(bm25) == 0


def test_scores_drop_when_only_common_words_match():
    """端到端效果:三个虚词全中,也压不过一个真关键词。

    这就是"问概念时不要冒出无关章节"的机制层保证。注意这里**不比倍数** ——
    倍数随语料规模变(真实语料上实测噪声降到 1/6,玩具库上只有几倍),
    钉一个"必须小 10 倍"的阈值只是在钉我这一天的语料,换一批数据就假红。
    """
    docs = _mixed_corpus()
    bm25 = _bm25(docs)
    apply_lucene_idf(bm25)
    noise = max(bm25.get_scores(["的", "是", "原理"]))          # 只问虚词
    real = max(bm25.get_scores(["稀有词A0"]))                   # 问真关键词
    assert noise < real, f"虚词还是能得分:虚词三连 {noise:.3f} vs 关键词 {real:.3f}"


# ─── 这条修法**不许**碰 FAQ ─────────────────────────────────────
def test_faq_retriever_keeps_the_original_idf():
    """FAQ 的 4.0 阈值是按旧 IDF 标定的,换公式等于**偷偷改阈值**。

    实测(改之前):「这周电路基础有作业吗」5.11 → 换 Lucene IDF 后 1.88,
    「Proteus 在哪下载」5.48 → 3.03 —— 真命中会掉到阈值以下,FAQ 通路直接哑掉。
    FAQ 那边解决同一个问题靠的是"实词闸门"(`_FAQ_STOPWORDS`),不是改打分。

    核对方式:拿同一批问答**另建一个没动过的** BM25Okapi,两者的 IDF 必须逐项相等。
    这样断言不依赖 rank_bm25 的内部细节,只表达"FAQ 走的是原始打分"。
    """
    pairs = [("电路基础作业的截止时间是什么时候?", "第8周周日 23:59")]
    pairs += [(f"占位问题{i}", f"占位答案{i}") for i in range(5)]
    r = FAQRetriever(pairs)
    stock = BM25Okapi([list(jieba.cut(q)) for q, _ in pairs])
    diff = {k: (stock.idf.get(k), v) for k, v in r.bm25.idf.items() if stock.idf.get(k) != v}
    assert not diff, (
        f"FAQ 的 IDF 被改过了({list(diff)[:5]}),那会让 4.0 阈值失效。"
        f"`apply_lucene_idf` 只该用在资料检索(BM25Retriever)上,别顺手加到 FAQRetriever")
