"""FAQ 解析、实词闸门、以及**真实常问问题.txt 的往返检查**。"""
from __future__ import annotations

from pathlib import Path

import jieba
import pytest

from src import config
from src.rag.retriever import FAQRetriever, _faq_content_tokens, _parse_faq

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

# 真实 FAQ 的路径。刻意**不从 config.FAQ_PATH 取**:`conftest.isolated_faq`
# 是 autouse 的,它会把 `config.FAQ_PATH` 指到临时文件上(否则单测里的
# 【记录】入口会往助教那份**手写、无备份**的常问问题.txt 里追加写)。
# 而下面那两条测试恰恰要在**真实文件**上跑,拿 FAQ_PATH 的话它们会:
#   skipif 在收集期读真文件(那时 fixture 还没生效)→ 不跳过,
#   测试体在运行期读临时文件 → 空库 → 莫名其妙地红。
# 直接写死真实路径,读取口径就只有一种。
_REAL_FAQ = config.WINDOWS_ROOT / "常问问题.txt"


# ─── 基本解析 ─────────────────────────────────────────────────
def test_parse_faq_basic():
    pairs = _parse_faq(SAMPLE_FAQ)
    assert len(pairs) == 3
    assert "proteus" in pairs[0][0].lower()
    assert "激活版" in pairs[0][1]
    assert "漏交" in pairs[1][0]
    assert "扣分" in pairs[1][1]


# ─── 冒号变体(2026-09-20 修的 bug)─────────────────────────────
# 原来切分答案那段写的是 `if nxt.startswith("Q:") or nxt.startswith("Q:")` ——
# 同一个表达式写了两遍(显然是想写第二个变体却复制错了),而漏掉的正是全角 `：`。
# 后果是**静默**的:整份全角 FAQ 被解析成一条,或者后一条的**问题被吞进前一条的答案**里。
_PAIRS = "甲\n甲答\n乙\n乙答\n"


@pytest.mark.parametrize("name,text", [
    ("半角", "Q: 甲\nA: 甲答\nQ: 乙\nA: 乙答\n"),
    ("全角", "Q：甲\nA：甲答\nQ：乙\nA：乙答\n"),          # 中文输入法下最容易打出来的写法
    ("半角Q配全角A", "Q: 甲\nA：甲答\nQ: 乙\nA：乙答\n"),
    ("全角Q配半角A", "Q：甲\nA: 甲答\nQ：乙\nA: 乙答\n"),   # 曾经会被吞掉的那一种
    ("冒号前有空格", "Q : 甲\nA : 甲答\nQ : 乙\nA : 乙答\n"),
    ("小写", "q: 甲\na: 甲答\nq: 乙\na: 乙答\n"),
    ("大小写混用", "Q: 甲\na: 甲答\nq: 乙\nA: 乙答\n"),
    ("空行分隔", "Q: 甲\nA: 甲答\n\nQ: 乙\nA: 乙答\n"),
])
def test_all_colon_variants_split_into_two_entries(name, text):
    """8 种写法都必须切出 2 条,且**第二条的问题不能跑进第一条的答案里**。"""
    pairs = _parse_faq(text)
    assert len(pairs) == 2, f"{name} 只切出 {len(pairs)} 条:{pairs}"
    assert pairs[1][0] == "乙", f"{name} 的第二条问题被吞了:{pairs}"
    assert "乙" not in pairs[0][1], f"{name} 把下一条吞进了上一条的答案:{pairs[0][1]!r}"


def test_multiline_answer_is_kept_as_one_entry():
    """答案里的空行是段落分隔,不该切条(后面没有紧跟 Q 的话)。"""
    pairs = _parse_faq("Q: 甲\nA: 第一段\n\n第二段\n\nQ: 乙\nA: 乙答\n")
    assert len(pairs) == 2
    assert "第一段" in pairs[0][1] and "第二段" in pairs[0][1]


def test_question_line_alone_is_dropped():
    """只有 Q 没有 A 的残条目直接丢掉,别产出一个空答复。"""
    assert _parse_faq("Q: 有问无答\n\nQ: 甲\nA: 甲答\n") == [("甲", "甲答")]


# ─── 实词闸门 ─────────────────────────────────────────────────
def test_content_tokens_strip_stopwords():
    """闸门用的分词要把虚词/疑问词去掉,只留实词。"""
    toks = _faq_content_tokens("作业什么时候交?")
    assert "作业" in toks
    assert "什么" not in toks and "时候" not in toks


# 假命中语料:1 条事务 FAQ + 12 条**刻意避开 `什么`/`是`/`时候`** 的填充条目。
#
# 填充条目为什么必须避开这三个词(这不是凑数,是在复现机制):假命中靠的是
# "只共享虚词",而虚词要拿到高分,前提是它在语料里 **df=1** —— 只出现在那条
# 事务 FAQ 里。实测:填充条目里一旦出现 `是`(比如「考试是开卷吗」),`是` 的
# IDF 立刻被拉平,原始最高分从 2.63 掉到 1.43。真实 FAQ 文件也是同理:
# 37 条里 `什么`/`是` 各只出现在少数条目中,所以那条假命中拿到 4.83 分。
_GATE_DEADLINE = ("电路基础作业的截止时间是什么时候?", "第8周周日 23:59")
_GATE_FILLERS = [
    ("proteus 在哪下载", "可能要自己找激活版"),
    ("作业漏交了怎么办", "可以直接发给我,第二次及以后补交酌情扣分"),
    ("PPT 有吗", "问张老师"),
    ("实验课在哪间教室", "看课表"),
    ("考试开卷吗", "闭卷"),
    ("实验报告要写几页", "两页以上"),
    ("数电实验箱怎么借", "找实验员"),
    ("补考怎么安排", "等通知"),
    ("成绩多久公布", "考后两周"),
    ("课程群号多少", "见公告"),
    ("预习报告要交吗", "不用"),
    ("能否迟交作业", "看情况"),
]
_GATE_CORPUS = [_GATE_DEADLINE] + _GATE_FILLERS

_FALSE_HIT_QUERY = "什么是竞争冒险"


def _raw_scores(r: FAQRetriever, q: str):
    """闸门**之前**的原始分 —— 也就是旧代码看的那份数据。"""
    return r.bm25.get_scores(list(jieba.cut(q)))


def test_gate_blocks_stopword_only_false_hit():
    """「什么是竞争冒险」只共享虚词却拿到正分 —— 拦住它的必须**是闸门**,不是分数不够。

    **这条测试的价值全在下面前三条断言上,少一条就退化成假绿。**
    它原来写的是单条语料:`BM25Okapi` 在语料只有 1 条时所有词的 IDF 都是负的,
    分数于是全负,先被 `search()` 里的 `scores[i] > 0` 滤掉了 —— 闸门压根没被考验过。
    那时把闸门整段删掉,这条测试**照样绿**。(假绿比没测更糟:它让人以为这里有保护。)

    换成 13 条语料后(实测原始最高分 2.63,为正):
      · 最高分那条确实是「截止时间」—— 假命中复现了;
      · 它的原始分 > 0 —— **旧路径会把这条 FAQ 返回给学生**;
      · 实词交集为空 —— 闸门的判据确实成立;
    三条合起来才推得出:`search()` 返回空**只可能**是闸门干的。
    """
    r = FAQRetriever(_GATE_CORPUS)
    scores = _raw_scores(r, _FALSE_HIT_QUERY)
    best = int(max(range(len(scores)), key=lambda i: scores[i]))
    assert r.qa_pairs[best][0] == _GATE_DEADLINE[0], \
        f"最高分换人了(现在是《{r.qa_pairs[best][0]}》),这组语料不再复现那个假命中"
    assert scores[best] > 0, (
        f"前提不成立了:原始分 {scores[best]:.3f} 不再是正数,"
        f"旧路径本来就会滤掉它 —— 这条测试不再能证明闸门有用"
    )
    assert not (r.content_tokens[best] & _faq_content_tokens(_FALSE_HIT_QUERY)), \
        "闸门判据不成立:这条 FAQ 与提问居然有实词交集"
    assert r.search(_FALSE_HIT_QUERY) == [], "闸门没拦住只共享虚词的假命中"


def test_gate_allows_real_admin_match():
    """正向对照:真该命中的事务问题不能被闸门误杀 —— 误杀等于把 FAQ 通路整个关掉。

    和上面那条用**同一组语料**:一拦一放,才说明闸门是"按实词有无"在判,
    而不是碰巧把所有东西都挡在外面(或都放进来)。
    """
    r = FAQRetriever(_GATE_CORPUS)
    hits = r.search("电路基础作业什么时候截止")
    assert hits, "真该命中的事务问题被闸门误杀了"
    assert "第8周" in hits[0]["answer"]


@pytest.mark.skipif(not _REAL_FAQ.exists(), reason="本机没有常问问题.txt")
def test_real_faq_gate_blocks_the_documented_false_hit():
    """在**真实** `常问问题.txt` 上复现 2026-09-20 记录的那个假命中。

    实测原始分 **4.83 ≥ 阈值 4.0** —— 也就是光靠阈值**挡不住**它:
    学生问「什么是竞争冒险」,系统会把「第8周周日 23:59」发过去。
    真正挡住它的是实词闸门,这条测试就是钉住"阈值不够、闸门必需"这件事。
    """
    r = FAQRetriever.from_file(_REAL_FAQ)
    assert r.qa_pairs, "FAQ 没解析出条目"
    best = float(max(_raw_scores(r, _FALSE_HIT_QUERY)))
    assert best >= config.FAQ_HIT_THRESHOLD, (
        f"前提变了:原始最高分只有 {best:.2f},已低于阈值 {config.FAQ_HIT_THRESHOLD} —— "
        f"FAQ 文件改动后假命中不再复现,这条测试要重新量一遍再决定去留"
    )
    assert r.search(_FALSE_HIT_QUERY) == [], "闸门没拦住真实语料上的假命中"


# ─── 检索 ─────────────────────────────────────────────────────
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


# ─── 真实文件往返(TA 手工编辑后最容易踩的坑)────────────────
@pytest.mark.skipif(not _REAL_FAQ.exists(), reason="本机没有常问问题.txt")
def test_real_faq_file_parses_and_no_answer_swallows_a_question():
    """拿**真实的** `常问问题.txt` 跑一遍。

    两条不变量:
      1. 能解析出条目(不是 0 条 —— 那说明文件的写法变了而解析器没跟上);
      2. **没有任何一条答案里残留 `Q:` / `Q：` 开头的行**。
         一旦残留,就说明有条目被静默吞掉了:后一条的**问题**变成了前一条**答案**
         的一部分,学生问那条问题时只会得到一个风马牛不相及的答复。
    助手/助教在中文输入法下编辑这个文件时打出全角 `：` 是迟早的事,
    这个测试就是为那一天准备的。
    """
    pairs = FAQRetriever.from_file(_REAL_FAQ).qa_pairs
    assert len(pairs) >= 20, f"只解析出 {len(pairs)} 条,文件写法是不是变了?"

    for q, a in pairs:
        for line in a.splitlines():
            assert not line.strip().startswith(("Q:", "Q：")), (
                f"答案里残留了下一条的 Q —— 条目被吞了。问题={q!r} 残留行={line!r}"
            )
