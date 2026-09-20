# -*- coding: utf-8 -*-
"""索引分块时的"上下文补全":`检索:` 锚点 和 `[教材 P{n}]` 页码标记。

**这两条是同一个病,修法也一样,所以放在一起测。**

`split_text` 是按段落合并的,而锚点行和页码标记都**自成一段** —— 于是它们只落进
所在页/所在文件的**第一块**,后面几块什么都没有:
  · 锚点丢了 → 学生问「1.36 怎么做」时,含解题步骤的那块得 0.00 分,答"资料里只有题目";
  · 页码丢了 → 模型拿不到页码,「引用教材时说清页码」对**近三分之二的教材内容**是空话
    (审计实测:教材 2106 块里只有 754 块含页码标记)。

修法都是"记住最近一次见到的,给没有的块补上"。这个文件就是钉住这两条。
"""
from __future__ import annotations

import pickle

from src.rag.indexer import _PAGE_MARK, build_index

# 一段足够长的正文,保证一页会被切成多块(每块约 400 字)
_PARA = "这是一段用来占位的中文正文内容。" * 18      # 约 288 字
_PAGE_BODY = "\n\n".join([_PARA] * 4)                 # 约 1150 字 → 3~4 块

_HEADER = ("【电路基础 教材(中文版第6版) 第1章 基本概念】\n"
           "来源:某扫描版.pdf 印刷页 P10-P11\n"
           "检索:电路基础 教材 第1章 基本概念 叠加定理\n")


def _build(tmp_path, text: str, name: str = "教材-第1章-基本概念.md") -> list[dict]:
    """写一个语料文件、建索引,返回分块列表。"""
    (tmp_path / name).write_text(text, encoding="utf-8")
    out = tmp_path / "idx.pkl"
    build_index(materials_dir=tmp_path, out_path=out, verbose=False)
    with out.open("rb") as f:
        return pickle.load(f)["chunks"]


def _two_page_text() -> str:
    return (f"{_HEADER}\n"
            f"[教材 P10]\n{_PAGE_BODY}\n\n"
            f"[教材 P11]\n{_PAGE_BODY}\n")


# ─── 页码标记 ─────────────────────────────────────────────────
def test_every_chunk_carries_a_page_marker(tmp_path):
    """一页被切成多块时,**每一块**都要有页码 —— 这是这轮修的核心不变量。"""
    chunks = _build(tmp_path, _two_page_text())
    assert len(chunks) > 3, f"样本没被切成多块({len(chunks)}),这个测试就没意义了"
    missing = [c["chunk_id"] for c in chunks if "[教材 P" not in c["text"]]
    assert not missing, f"这些块拿不到页码,模型说不出「教材第几页」:{missing}"


def test_page_marker_advances_to_the_next_page(tmp_path):
    """翻页后补的必须是**新**页码,不能一直补第一页的。

    补错的代价:模型会说"教材第10页讲了傅里叶变换" —— 一个**看起来对**的错误指引,
    比不说页码更糟,学生翻过去发现不对就不信这个功能了。
    """
    chunks = _build(tmp_path, _two_page_text())
    pages = []
    for c in chunks:
        first = c["text"].split("[教材 P")[1][:3]
        pages.append(int("".join(ch for ch in first if ch.isdigit())))
    assert 10 in pages and 11 in pages, f"页码序列={pages}"
    assert pages == sorted(pages), f"页码必须单调不减(往后的块不该回到前页):{pages}"
    assert pages[-1] == 11, f"最后一块应当属于第 11 页:{pages}"


def test_non_textbook_file_gets_no_page_marker(tmp_path):
    """数电的 PDF 没有教材页码 —— 这条改动对它们必须是**完全无操作**的。"""
    chunks = _build(tmp_path, "【数电实验】\n检索:数电 实验 Proteus 仿真\n\n"
                              + _PAGE_BODY, name="数电实验指导.md")
    assert chunks
    assert all("[教材 P" not in c["text"] for c in chunks)


def test_truncated_page_marker_is_still_recognized():
    """标记可能正好被 overlap 切开(`…[教材 P34` + `0]…`),不带尾括号才认得出。"""
    assert _PAGE_MARK.search("正文…[教材 P34") is not None
    assert _PAGE_MARK.search("正文…[教材 P340]").group(1) == "340"


# ─── 检索锚点 ─────────────────────────────────────────────────
def test_anchor_is_copied_into_every_chunk(tmp_path):
    """锚点必须复制进每一块 —— 这是「1.36 怎么做」能答出来的原因。

    原来的样子:锚点只落在第 0 块,于是含**解题步骤**的第 2 块得 0.00 分,
    top-5 被别的文件的标题块占满,模型如实回答"资料里只有题目,没有解题步骤"。
    """
    chunks = _build(tmp_path, _two_page_text())
    for c in chunks:
        assert "检索:电路基础 教材 第1章 基本概念 叠加定理" in c["text"], \
            f"第 {c['chunk_id']} 块丢了锚点"


def test_anchor_and_page_marker_both_survive_in_the_same_chunk(tmp_path):
    """两条补全不能互相挤掉:同一块里既要能召回(锚点)又要能引用页码。"""
    chunks = _build(tmp_path, _two_page_text())
    tails = chunks[1:]
    assert tails, "至少要有第二块才谈得上「补进去」"
    for c in tails:
        assert "检索:" in c["text"] and "[教材 P" in c["text"], \
            f"第 {c['chunk_id']} 块只补上了一样:{c['text'][:80]!r}"


def test_first_chunk_is_not_double_annotated(tmp_path):
    """第 0 块本来就带着锚点,不能再补一遍 —— 重复会让这些词的词频虚高。"""
    chunks = _build(tmp_path, _two_page_text())
    assert chunks[0]["text"].count("检索:电路基础 教材 第1章 基本概念 叠加定理") == 1
