# -*- coding: utf-8 -*-
"""中文版作业答案切题(`split_cn_problems`)的回归测试。

这份 PDF 是任课老师指定的**权威版**课后题答案,切错了会把答案张冠李戴,
所以下面四条边界都是 2026-09-20 在真文件上踩出来的:

  1. **结尾不能用 `\\b`**。Python 的 `\\w` 把中文也算进去,"2.74下图所示电路" 里
     "4" 和 "下" 都是 `\\w`,用 `\\b` 判定词尾会**漏掉这一题**。改用后面不能跟数字。
  2. **行首的纯数值不是题号**。"10.5 V"、"0.25" 这种也算行首数字,得靠
     "题号后面十几个字里有中文" 把它们滤掉。
  3. **题号后面必须有本文件的章号**。第 10 章的文件里会冒出行首的 "3.92",
     第 11 章里会冒 "13.7"(都是引用别题),按文件名声明的章号一过滤就没了。
  4. **同一题号出现多次取最长的那段**。第 14 章开头有一行"作业题 14.0 … 14.4 …"
     的清单,pdfplumber 换行后清单项也落在行首,先匹配到的是清单而不是正文。
"""
from scripts.import_circuit_theory import (
    _expected_chapters,
    _fallback_dir,
    split_cn_problems,
)


# ─── 边界 1:中文紧跟数字,`\b` 会漏 ────────────────────────────────

def test_number_followed_directly_by_chinese_is_found():
    text = "2.74下图所示电路用于控制发动机的转速，求串联降压电阻。\n解：R = 1.17"
    assert [p for p, _ in split_cn_problems(text, {2})] == ["2.74"]


# ─── 边界 2:行首纯数值/公式不算题号 ────────────────────────────────

def test_line_start_numbers_without_chinese_are_ignored():
    text = (
        "10.5 V\n"
        "0.25\n"
        "3.6\n"
        "10.3 求下图所示电路中各支路电流。\n"
        "解：I = 2 A\n"
    )
    assert [p for p, _ in split_cn_problems(text, {10})] == ["10.3"]


# ─── 边界 3:按文件名声明的章号过滤别题引用 ─────────────────────────

def test_numbers_from_other_chapters_are_filtered_out():
    text = (
        "3.92 参见第3章的做法。\n"
        "13.7 求频率响应。\n"
        "10.3 求下图所示电路中各支路电流。\n"
        "解：I = 2 A\n"
    )
    # 这份文件被声明为"只有第 10 章",3.92 和 13.7 都是引用,不该成篇
    assert [p for p, _ in split_cn_problems(text, {10})] == ["10.3"]


# ─── 边界 4:连字符题号 + 同题号取最长 ──────────────────────────────

def test_hyphen_separator_is_normalized_to_dot():
    """第 6 章整章写 `6-11`,要和英文版的 6.11.md 合并,必须归一成 6.11。"""
    text = "6-11 求下图所示电路的等效电感。\n解：L = 40 mH"
    assert [p for p, _ in split_cn_problems(text, {6})] == ["6.11"]


def test_duplicate_number_keeps_the_longest_section():
    """14.0 先出现在开头的作业清单里,真正的正文在后面 —— 取最长的那个。"""
    text = (
        "14.0 画出本章思维导图\n"
        "14.4 传递函数\n"
        "14.0 画出本章思维导图\n"
        "解：本章讲频率响应、滤波电路、谐振等内容，思维导图按这三块展开即可。\n"
    )
    got = dict(split_cn_problems(text, {14}))
    assert "本章讲频率响应" in got["14.0"]
    assert got["14.0"] != "14.0 画出本章思维导图"


def test_sections_are_sorted_numerically():
    text = "2.82 甲的正文。\n2.33 乙的正文。\n2.74 丙的正文。"
    assert [p for p, _ in split_cn_problems(text, {2})] == ["2.33", "2.74", "2.82"]


def test_empty_text_returns_nothing():
    assert split_cn_problems("", {1}) == []


# ─── 文件名 → 章号 ────────────────────────────────────────────────

def test_expected_chapters_from_filenames():
    # 真实文件名,一个都不能读错(读错就没法过滤噪声)
    assert _expected_chapters("Z第1-2章 作业题目及答案.pdf") == {1, 2}
    assert _expected_chapters("Z第3-4章 作业题目及答案.pdf") == {3, 4}
    assert _expected_chapters("Z第6章答案.pdf") == {6}
    assert _expected_chapters("Z第9-第9章 作业答案.pdf") == {9}
    assert _expected_chapters("Z第10第10章 作业答案).pdf") == {10}
    assert _expected_chapters("Z第14章 作业题目及答案.pdf") == {14}


def test_fallback_dir_matches_existing_convention():
    """兜底目录名要和已有语料目录(`1第一章`)同形状,不能造出平行的 `1第1章`。"""
    assert _fallback_dir(1) == "1第一章"
    assert _fallback_dir(10) == "10第十章"
    assert _fallback_dir(19) == "19第十九章"
