"""文本分块测试。"""
from __future__ import annotations

from src.utils.chunker import split_text


def test_split_short_text():
    assert split_text("简短文本。") == ["简短文本。"]


def test_split_empty():
    assert split_text("") == []


def test_split_respects_size():
    text = ("这是一个测试句子。" * 50)  # 约 350 字
    chunks = split_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    for c in chunks[:-1]:  # 最后一块可能短
        assert len(c) <= 100 + 20  # 允许一点弹性


def test_split_preserves_content():
    text = "段落一。段落一第二句。\n\n段落二内容在这里。"
    chunks = split_text(text, chunk_size=1000, overlap=0)
    combined = "".join(chunks)
    assert "段落一" in combined
    assert "段落二" in combined
