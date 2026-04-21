"""FAQ 解析和检索测试。"""
from __future__ import annotations

from pathlib import Path

from src.rag.retriever import FAQRetriever, _parse_faq

SAMPLE_FAQ = """Q: 
学长学长,请问今天实验课上的仿真软件proteus在哪里下载?
A:
 可能要自己找激活版使用

Q:
学长好,我的作业漏交了
想问问直接发给学长嘛
A:
可以直接发给我,第二次及以后的补交会酌情扣分

Q:
PPT 有吗
A:
问张老师
"""


def test_parse_faq_basic():
    pairs = _parse_faq(SAMPLE_FAQ)
    assert len(pairs) == 3
    assert "proteus" in pairs[0][0].lower()
    assert "激活版" in pairs[0][1]
    assert "漏交" in pairs[1][0]
    assert "扣分" in pairs[1][1]


def test_faq_retriever_search(tmp_path: Path):
    f = tmp_path / "faq.txt"
    f.write_text(SAMPLE_FAQ, encoding="utf-8")
    r = FAQRetriever.from_file(f)
    hits = r.search("proteus 在哪里下载", top_k=3)
    assert hits
    assert "proteus" in hits[0]["question"].lower()
    assert hits[0]["score"] > 0


def test_faq_retriever_empty_query():
    r = FAQRetriever([])
    assert r.search("随便") == []
