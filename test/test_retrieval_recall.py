# -*- coding: utf-8 -*-
"""检索质量的回归测试:一批「学生真会问的」提问,各自必须召回对的资料。

**为什么要有这个文件(单测里已经有一堆锚点/分块的测试了)。**
那些测的是"索引里有没有那块内容";这里测的是**"学生问出来,能不能召回到"**。
两者中间隔着 BM25 的打分与排序 —— 2026-09-20 就是在这层抓到问题的:
`什么是叠加原理` 把 `教材-第5章-运算放大器.md` 排到了正确章节前面
(13.53 vs 13.47),原因是 `rank_bm25` 把"半数以上分块都含"的词的 IDF
统一兜底成正数,于是 `什么/是/原理` 三个虚词给每一章白送 7.3 分。
只看"索引里有没有"的测试**永远发现不了这种错**。

用例表放在 `scripts/eval_retrieval.py` 的 `CASES`(评测脚本和这个测试**共用一张表**):
调参时要看分数和名次的移动,用脚本;要一道闸门"过了就不许退化",用这个测试。
表里的期望值是量出来的,`max_foreign` 是**当前实测的噪声预算**(涨了才红)。

索引不存在时**跳过**(而不是失败):CI/新机器上还没建索引是正常状态。
"""
from __future__ import annotations

import pytest

from src import config
from scripts.eval_retrieval import CASES, evaluate, verdict

pytestmark = pytest.mark.skipif(
    not config.BM25_INDEX_PATH.exists(),
    reason=f"没有索引({config.BM25_INDEX_PATH}),先跑 `python scripts/build_index.py`",
)


@pytest.fixture(scope="module")
def retriever():
    """整个模块共用一个检索器(装载 + jieba 初始化约 1~2 秒,别每条用例重来)。"""
    from src.rag.retriever import BM25Retriever
    try:
        return BM25Retriever.load(config.BM25_INDEX_PATH)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"索引装载失败({e})—— 多半是索引和数据版本对不上,重建即可")


@pytest.mark.parametrize("case", CASES, ids=[c["q"] for c in CASES])
def test_query_retrieves_the_expected_sources(retriever, case):
    """每条用例:期望的资料要进 top-K、不该出现的不能出现、跨章噪声不超预算。

    失败时打出的信息包含**实际召回了什么**(`evaluate` 把 top-K 连同分数都带回来了),
    这样看 CI 日志就能判断是"检索坏了"还是"期望写错了"。
    """
    result = evaluate(retriever, case, config.RAG_TOP_K)
    ok, problems = verdict(result, case)
    top = " / ".join(f"{r['score']:.2f} {r['source']}" for r in result["top"]) or "(一条都没召回)"
    assert ok, (
        f"提问「{case['q']}」不达标:\n  " + "\n  ".join(problems)
        + f"\n实际 top-{config.RAG_TOP_K}:{top}"
        + (f"\n这条用例守的是:{case['note']}" if case["note"] else "")
    )


def test_no_duplicate_queries_in_the_case_table():
    """表里同一个提问不能出现两次 —— 否则 parametrize 的 id 会重名,看着像跑了两条。"""
    qs = [c["q"] for c in CASES]
    dupes = {q for q in qs if qs.count(q) > 1}
    assert not dupes, f"用例表里有重复提问:{dupes}"


def test_every_case_has_something_to_expect():
    """每条用例至少要有 `must_hit` 或 `must_hit_re`,不然它永远绿、白占一行。"""
    for c in CASES:
        assert c["must_hit"] or c["must_hit_re"], f"「{c['q']}」没有期望命中的资料"


def test_expected_sources_actually_exist_in_the_index(retriever):
    """期望值不能是**拼错的文件名** —— 写错了的期望会永远不命中,而人是不会发现的。

    这条只在有索引时能查,所以放在这里(`must_hit_re` 是正则,没法逐个核对,跳过)。
    """
    names = {c["source"] for c in retriever.chunks}
    missing = {s for case in CASES for s in case["must_hit"] if s not in names}
    assert not missing, f"用例表里写了索引里没有的文件名:{sorted(missing)}"
