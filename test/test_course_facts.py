"""课程事务事实表:解析、生效期过滤、渲染。

**为什么这个文件值得存在。**
单看它就是个文本解析器,写错了顶多"这条不生效"。但它有个特殊地位:
**它可以覆盖 FAQ 和课程资料** —— 而提示词第②条("事务类一个字都不许猜")
整条防线的前提是"资料里没有 就 说不知道"。事实表是**唯一一处人写的字会绕过
第②条、直接以权威口径说出来**的地方。

所以它的边界必须是钉住的,不能靠"一般不会写错":
  · 生效日/失效日**当天算不算** —— 学期最后一天就有人踩;
  · 日期写错时是"当成长期有效"还是"整块跳过" —— 猜错方向就是把一条
    自己都算不清还算不算数的规则当成权威发出去;
  · 写错的时候**必须出声**(告警),不能静默丢弃 —— 静默丢弃 =
    助教以为写了、其实没生效,而没有任何痕迹可查。
"""
from __future__ import annotations

import textwrap
from datetime import date

from src.utils.course_facts import (
    Fact,
    load_facts,
    parse_facts,
    render_facts,
)


# ─── 解析 ─────────────────────────────────────────────────────
def test_parse_full_block():
    facts = parse_facts(textwrap.dedent("""\
        生效: 2026-09-01
        失效: 2027-01-31
        事实: 补交需在截止后 7 天内联系助教登记
        来源: 张曰理老师 2026-09-01 课间通知
    """))
    assert len(facts) == 1
    f = facts[0]
    assert f.text == "补交需在截止后 7 天内联系助教登记"
    assert f.source == "张曰理老师 2026-09-01 课间通知"
    assert f.effective_from == date(2026, 9, 1)
    assert f.effective_until == date(2027, 1, 31)


def test_parse_comments_and_multiple_blocks():
    """`#` 注释整行丢掉;空行分块;注释夹在块中间也不能把一块劈成两块。"""
    facts = parse_facts(textwrap.dedent("""\
        # 这是文件头注释
        事实: 第一条

        # 注释夹在中间
        事实: 第二条
        来源: 某 PPT

        事实: 第三条
    """))
    assert [f.text for f in facts] == ["第一条", "第二条", "第三条"]


def test_parse_accepts_fullwidth_colon_and_chinese_date():
    """助教在中文输入法下会打出 `：` 和 `2026年9月1日` —— 这俩必须认。

    `常问问题.txt` 的解析器就栽在全角冒号上过(见 retriever._parse_faq 的注释:
    同一个表达式写了两遍,漏掉了全角变体,两条 FAQ 被并成一条,还静默不报错)。
    """
    facts = parse_facts("生效：2026年9月1日\n事实：中文冒号也要认\n")
    assert len(facts) == 1
    assert facts[0].effective_from == date(2026, 9, 1)
    assert facts[0].text == "中文冒号也要认"


def test_dates_accept_several_separators():
    for raw in ("2026-09-01", "2026/9/1", "2026.9.1", "2026年9月1日", "2026-9-1"):
        facts = parse_facts(f"生效: {raw}\n事实: x\n")
        assert facts and facts[0].effective_from == date(2026, 9, 1), raw


def test_block_without_fact_text_is_dropped():
    """只有日期、没有「事实」的块 → 丢掉(它没有内容可用)。"""
    assert parse_facts("生效: 2026-09-01\n") == []


def test_unparseable_date_drops_the_whole_block_and_warns(capsys):
    """**日期写错 → 整块跳过 + 告警**,不能当成"长期有效"。

    这是最关键的一条边界:如果按"解析不出来就当没有日期"处理,
    一条本想限制在本学期的规则会变成**永久有效**,而且没人会发现。
    反方向(跳过)最坏不过是退回"资料里没有"这个安全行为。
    """
    facts = parse_facts("生效: 2026-13-45\n事实: 日期不存在的规则\n")
    assert facts == []
    err = capsys.readouterr().err
    assert "整块跳过" in err and "2026-13-45" in err, "必须出声,不能静默丢弃"


def test_expiry_before_start_drops_the_block_and_warns(capsys):
    facts = parse_facts("生效: 2027-01-01\n失效: 2026-01-01\n事实: 前后写反了\n")
    assert facts == []
    assert "早于" in capsys.readouterr().err


def test_unknown_line_is_ignored_but_the_fact_survives(capsys):
    """认不出的行只忽略那一行,不牵连整块(宽容解析:错别字不该废掉一条事实)。"""
    facts = parse_facts("生效: 2026-09-01\n事实: 有用的一条\n备注: 随手写的\n")
    assert [f.text for f in facts] == ["有用的一条"]
    assert "认不出的行" in capsys.readouterr().err


def test_bad_block_does_not_take_down_its_neighbours():
    """一块坏不能带走好的一整块 —— 这是"文件里混进一条笔误"的常态。"""
    facts = parse_facts(textwrap.dedent("""\
        事实: 好的第一条

        生效: 乱七八糟
        事实: 坏的这条

        事实: 好的第二条
    """))
    assert [f.text for f in facts] == ["好的第一条", "好的第二条"]


# ─── 生效期 ───────────────────────────────────────────────────
def _fact(**kw) -> Fact:
    return Fact(text="x", **kw)


def test_no_dates_means_always_effective():
    f = _fact()
    assert f.is_effective(date(1999, 1, 1))
    assert f.is_effective(date(2099, 12, 31))


def test_boundaries_are_inclusive_on_both_ends():
    """生效日与失效日**当天都算有效**(含两端)。

    含不含当天平时看不出来,学期最后一天就会有人踩 —— 所以钉成"含"。
    """
    f = _fact(effective_from=date(2026, 9, 1), effective_until=date(2027, 1, 31))
    assert f.is_effective(date(2026, 9, 1)), "生效当天应当已生效"
    assert f.is_effective(date(2027, 1, 31)), "失效当天应当还没失效"
    assert not f.is_effective(date(2026, 8, 31)), "生效前一天不该生效"
    assert not f.is_effective(date(2027, 2, 1)), "失效后一天不该再生效"


def test_load_facts_filters_by_today(tmp_path):
    p = tmp_path / "课程事务.txt"
    p.write_text(textwrap.dedent("""\
        失效: 2026-01-31
        事实: 上学期的老规则

        生效: 2026-09-01
        失效: 2027-01-31
        事实: 本学期的规则

        生效: 2027-02-20
        事实: 下学期的规则

        事实: 长期规则
    """), encoding="utf-8")

    got = [f.text for f in load_facts(p, today=date(2026, 9, 20))]
    assert got == ["本学期的规则", "长期规则"], "过期的不注入、还没生效的也不注入"


def test_load_facts_missing_file_is_empty_not_an_error(tmp_path):
    """文件不存在 = 功能静默休眠。数据文件没放上去是这个项目的常态,
    不能因此让整条答疑挂掉(与 FAQRetriever.from_file 的处理一致)。"""
    assert load_facts(tmp_path / "没有这个文件.txt") == []


# ─── 渲染 ─────────────────────────────────────────────────────
def test_render_facts_shape():
    out = render_facts([
        Fact(text="补交需 7 天内登记", source="张老师通知"),
        Fact(text="长期规则"),
    ])
    assert out.startswith("【课程事务(现行)】")
    assert "· 补交需 7 天内登记(来源:张老师通知)" in out
    assert "· 长期规则" in out
    assert "长期规则(" not in out, "没有来源时不该硬凑一个空括号"
