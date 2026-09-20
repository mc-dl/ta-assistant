"""`scripts/eval_admin_fidelity.py` 里**不联网的那部分**:数字抽取与 RAG 禁令。

**为什么值得单测一个"评测脚本"。** 那个脚本靠"两边数字集合一致"来判"润色有没有失真",
而这个判断的**全部精度**都在 `digits()` 里 —— 它一旦抽错,结论就跟着错,
而且是往**"看着没问题"**的方向错。

这不是假设:第一版我就照着它的输出写了"数字一条都没丢也没被改",
复核才发现抽出来的"多出来的数字"里大半是「一下」「一起」这种**中文数字当普通字用**。
所以这里把它的**已知噪声**钉成测试 —— 不是为了"它没错",而是为了
**下次谁看到 18 条"多出来"时,能立刻知道其中哪些类属于噪声**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_admin_fidelity as eaf  # noqa: E402


class TestDigits:
    def test_arabic_numbers_are_collected(self):
        assert eaf.digits("第 8 周交,7 天内") == {"8", "7"}

    def test_chinese_numerals_are_normalized_to_arabic(self):
        """「第八周」和「第8周」是同一个意思,不然会把同义换算误判成失真。"""
        assert eaf.digits("第八周") == eaf.digits("第8周") == {"8"}
        assert eaf.digits("第十周") == {"10"}

    def test_bare_chinese_numerals_are_a_known_false_positive(self):
        """⚠️ **已知噪声,不是 bug,但必须记住它存在。**

        「一下」「一起」「一般」里的"一"没有数量含义,它却会被算成数字 1。
        实测里 18 条"多出来"绝大多数是这一类的误报 ——
        所以看到"多了"要去**看上下文**,不能直接当成"模型编了数字"。
        这条测试的作用就是**把这个噪声钉在明面上**。
        """
        assert eaf.digits("再确认一下") == {"1"}
        assert eaf.digits("一般") == {"1"}

    def test_digits_that_are_actually_the_same_stay_equal(self):
        """回归:FAQ 原文与答复写法不同、含义相同时,不该被判成不一致。"""
        assert eaf.digits("截止后 7 天") == eaf.digits("截止后七天")

    def test_no_numbers_gives_empty_set(self):
        assert eaf.digits("作业交给学委") == set()


class TestNoRAG:
    def test_touching_rag_is_an_error(self):
        """admin + FAQ 命中这条路**不应该**查资料 —— 碰了就该炸出来。

        这是脚本里唯一一条"断言代码行为"的部分;少了它,哪天多调一次 RAG
        只会让结果看起来"更详细",而**没有任何地方会报错**。
        """
        with pytest.raises(AssertionError, match="不应该查资料"):
            eaf.NoRAG().search("随便问问")
