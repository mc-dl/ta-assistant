# -*- coding: utf-8 -*-
"""扫描版教材装配(`import_textbook`)的回归测试。

这块最容易出事的地方**不是代码,是那张表**:

  - `CHAPTERS` 里某个起始页打错一位 → 那一章的正文会被切到隔壁章去,而且**不报错**,
    只是内容悄悄错位;
  - `PDF_TO_PRINTED = 16` 是"印刷页 = PDF 页 - 16",错了的话学生按答案里的
    "教材 P188" 翻书会翻到别的题(错误集锦就是按书上页码写的);
  - 目录页(列着全部章名页码)一旦进语料,会变成"谁提问它都高分"的搅局者
    (见 `docs/knowledge_base.md` §3.4.1)。

所以下面的测试重点是**守住这些不变量**,而不是逐行覆盖实现。
"""
import pytest

from scripts.import_textbook import (
    APPENDIX_START,
    CHAPTERS,
    CONCEPTS,
    FRONT_MATTER_LAST_PDF,
    PDF_TO_PRINTED,
    TRANSLATOR_NOTE_PDFS,
    _FILE_PREFIX,
    _SCANNER_META,
    _chapter_header,
    _fix_ocr_symbols,
    _page_ok,
    _render,
    _strip_running_header,
    _translator_note,
)


# ─── 表的不变量(改 CHAPTERS / CONCEPTS 时必须仍然过)──────────────

def test_all_19_chapters_present_and_ordered():
    assert [ch for _p, ch, _t in CHAPTERS] == list(range(1, 20))
    starts = [p for p, _c, _t in CHAPTERS]
    assert starts == sorted(starts), "起始页必须递增"


def test_chapter_ranges_do_not_overlap_or_leave_gaps():
    """章与章必须首尾相接:上一章结束的下一页就是下一章的开始。

    不检查的话,把某章起始页写小一格不会报错,只会让那一章的正文里混进上一章的尾巴。
    """
    for (_lo, ch, _t), (nlo, nch, _nt) in zip(CHAPTERS, CHAPTERS[1:]):
        assert nlo > _lo, f"第{ch}章与第{nch}章起始页反了"
    assert CHAPTERS[0][0] == 2, "第1章的正文从印刷页 2 开始(第 1 页是章名页)"
    assert APPENDIX_START > CHAPTERS[-1][0]


def test_every_chapter_has_concepts():
    """锚点里的概念词是检索的主力,漏一章等于那一章只能靠正文裸奔。"""
    for _p, ch, title in CHAPTERS:
        assert CONCEPTS.get(ch), f"第{ch}章 {title} 没有概念词"
    assert set(CONCEPTS) == {ch for _p, ch, _t in CHAPTERS}


def test_concept_keywords_do_not_contain_structural_headings():
    """概念词是"学生真会问的",不是书的目录结构。

    "本章小结""复习题"这类词每个学生每章都问得出口,写进锚点只会把无关提问吸过来。
    """
    banned = ("本章小结", "复习题", "习题", "综合理解题", "PSpice", "MATLAB", "引言")
    for ch, words in CONCEPTS.items():
        for w in words:
            assert not any(b in w for b in banned), f"第{ch}章概念词里混进了结构标题:{w}"


def test_front_matter_is_excluded_including_toc():
    """书前 16 页(封面/版权/译者序/前言/作者简介/目录)不进正文。

    目录页尤其:它列着全部章名和页码,进了索引就是第二个"00_资料总目录"。
    """
    assert FRONT_MATTER_LAST_PDF == 16
    # CHAPTERS 里存的是**印刷页**,换回 PDF 页才能和"书前 16 页"比
    assert CHAPTERS[0][0] + PDF_TO_PRINTED > FRONT_MATTER_LAST_PDF


# ─── 页码偏移与书眉自查 ───────────────────────────────────────────

@pytest.mark.parametrize("pdf_no,printed", [(19, 3), (20, 4), (21, 5), (686, 670)])
def test_page_offset_matches_the_real_book(pdf_no, printed):
    """这几个是**实测**出来的(书上书眉印的就是这些数)。"""
    assert pdf_no - PDF_TO_PRINTED == printed


def test_page_ok_accepts_matching_header():
    ok, why = _page_ok(19, "第1章基本概念3\n正文…")
    assert ok and "3" in why


def test_page_ok_flags_mismatched_header():
    ok, _why = _page_ok(19, "第1章基本概念99\n正文…")
    assert not ok


def test_page_ok_passes_when_no_header():
    """整页图/章首页没有书眉 —— 不判,不能当成错误刷屏。"""
    ok, _ = _page_ok(19, "这是一整页的图，没有书眉")
    assert ok


# ─── 去书眉 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("head", [
    "第1章基本概念3",
    "4第一部分直流电路",
    "第10章正弦稳态分析315",
])
def test_running_header_is_stripped(head):
    assert _strip_running_header(head + "\n正文第一行") == "正文第一行"


def test_body_line_that_merely_mentions_a_chapter_is_kept():
    """正文里提到"第2章"很常见 —— 只有**短行且像书眉**才删,别把正文吃了。"""
    body = "如第2章所述，欧姆定律描述了电压与电流的关系，这里再补充一点："
    assert _strip_running_header(body) == body


# ─── 装配 ────────────────────────────────────────────────────────

def _pages(mapping):
    """{印刷页: 正文} → 假的 OCR 缓存 {PDF页: 正文}。"""
    return {p + PDF_TO_PRINTED: t for p, t in mapping.items()}


def test_render_puts_a_printed_page_marker_before_every_page():
    """`[教材 P188]` 是答案能引用"书上第几页"的前提,少了它等于没有页码。"""
    pages = _pages({98: "4.3叠加定理\n叠加定理说的是…", 99: "例题4.4…"})
    text, bad, _skipped = _render("表头\n", pages, 98, 99)
    assert "[教材 P98]" in text and "[教材 P99]" in text
    assert "4.3叠加定理" in text and "例题4.4" in text
    assert text.index("[教材 P98]") < text.index("叠加定理说的是")
    assert bad == []


def test_render_skips_pages_missing_from_cache_without_crashing():
    """OCR 断了会缺页 —— 缺就缺,不能把整章搞崩,也不要留个空标记充数。"""
    pages = _pages({98: "有内容", 100: "后面这页有"})
    text, _bad, _skipped = _render("表头\n", pages, 98, 100)
    assert "[教材 P98]" in text and "[教材 P100]" in text
    assert "[教材 P99]" not in text


def test_render_reports_pages_whose_header_disagrees():
    pages = _pages({98: "第4章电路定理99\n正文"})   # 书眉写着 99,应该是 98
    _text, bad, _skipped = _render("表头\n", pages, 98, 98)
    assert bad and "PDF114" in bad[0]


def test_chapter_header_carries_chapter_title_and_concepts():
    """锚点行必须能被 jieba 切出"第4章""电路定理""叠加定理"这些词。"""
    h = _chapter_header(4, "电路定理", 96, 132, CONCEPTS[4])
    assert "第4章" in h and "电路定理" in h
    assert "叠加定理" in h and "戴维南定理" in h
    assert "检索:" in h
    assert h.count("叠加定理") == 1, "锚点里的词不该重复,重复只会虚高词频"


def test_chapter_header_survives_a_chapter_with_no_concepts():
    """防御:CONCEPTS 万一漏了某章,表头也得能拼出来(测试会另外红,但不能崩)。"""
    h = _chapter_header(19, "二端口网络", 628, 669, [])
    assert "第19章" in h and "检索:" in h


# ─── 文件名 ──────────────────────────────────────────────────────

# `materials/电路基础/讲义/` 里**真实存在**的文件名(抄自目录 ls)
_SLIDE_NAMES = {
    "第1章-基本概念.md", "第2章-基本定律.md", "第3章-分析方法.md",
    "第4章-电路定理.md", "第6章-电容和电感.md", "第7章-一阶电路.md",
    "第8章-二阶电路分析.md", "第9章-正弦量与相量.md",
    "第10章-正弦稳态分析.md", "第11章-交流功率分析.md",
    "第14章-频率响应.md",
}


def test_chapter_filenames_do_not_collide_with_the_lecture_slides():
    """教材章文件名**必须**和讲义的不重名。

    讲义的文件名是 `第4章-电路定理.md` 这种格式,而教材的章名**逐字相同** ——
    索引里的 `source` 只存文件名不含目录,重名的话学生看到
    「参考:第4章-电路定理.md」分不出那是课堂 PPT 还是教材正文。
    """
    textbooks = {f"{_FILE_PREFIX}第{ch}章-{title}.md" for _p, ch, title in CHAPTERS}
    assert not (textbooks & _SLIDE_NAMES), \
        f"教材与讲义文件名撞了:{textbooks & _SLIDE_NAMES}"
    assert len(textbooks) == len(CHAPTERS), "教材文件名之间也不能重名"


# ─── 译者序(书前页)────────────────────────────────────────────────

def test_translator_note_never_gets_a_negative_page_marker():
    """书前 16 页是**罗马数字**页码,套 "印刷页 = PDF 页 - 16" 会算出 P-12 这种负页码。

    实测踩到过:学生按 `[教材 P-12]` 翻书永远翻不到。书前页一律不加页码标记。
    """
    pages = {p: f"译者序第{p}页正文" for p in TRANSLATOR_NOTE_PDFS}
    text = _translator_note(pages)
    assert text, "TRANSLATOR_NOTE_PDFS 里的页都在缓存里,应当产出正文"
    assert "P-1" not in text and "P-" not in text, f"出现了负页码:{text[:400]}"
    assert "译者序第5页正文" in text
    assert "检索:" in text


def test_translator_note_is_a_single_page_not_the_publisher_foreword():
    """只收 PDF 5(译者序),**不能**收 PDF 4(出版者的话)。

    早先误写成 (4, 5),产出物开头是"出版者的话"却自称"符号与国标差异" ——
    一个自称讲 A 实际是 B 的文件比没有文件更糟。
    """
    assert TRANSLATOR_NOTE_PDFS == (5,)


def test_translator_note_is_omitted_when_the_page_is_missing():
    """OCR 缺页时宁可不产出,也不要产出一个空壳文件。"""
    assert _translator_note({}) == ""


# ─── 扫描仪元信息页(2026-09-20 语料审计发现)────────────────────

# 实测原文(超星导出附在扫描件最后一页):
_SCANNER_PAGE = ("[ Gener al  I nf or nat i on]\n"
                 "书名=14536644_电路基础 原书第6版=FUNDAMENTALSOFELECTRICCIRCUITS\n"
                 "页数=693  SS号=14536644")


def test_scanner_metadata_page_is_dropped_entirely():
    """扫描仪的元信息页不是书的内容,**整页丢掉**,连页码标记都不该留。

    它原来被打上 `[教材 P695]` 进了索引 —— 于是模型可以把
    "页数=693 SS号=14536644" 当成"教材第 695 页写着什么"讲给学生。
    """
    pages = _pages({98: "4.3叠加定理\n正文…", 679: _SCANNER_PAGE})
    text, _bad, skipped = _render("表头\n", pages, 98, 679)
    assert "SS号" not in text and "14536644" not in text
    assert "[教材 P679]" not in text, "整页丢掉了,不该还留一个指向它的页码标记"
    assert skipped and "PDF695" in skipped[0]


def test_scanner_metadata_detection_tolerates_ocr_spacing():
    """OCR 会在英文单词里塞空格(`Gener al  I nf or nat i on`),匹配要容忍。"""
    assert _SCANNER_META.search(_SCANNER_PAGE)
    assert _SCANNER_META.search("页数=693")
    assert _SCANNER_META.search("SS号=14536644")


@pytest.mark.parametrize("text", [
    "4.3 叠加定理:求图 4-21 中的电流。",
    "习题 4.5 已知 R=10Ω,求功率。",
    "图 4-21 习题 4.5 图",
    "本页共 3 题",          # 有"页"有数字,但不是 `页数=数字` 这种元信息格式
    "第 4 章共 30 页",
])
def test_normal_page_is_not_mistaken_for_scanner_metadata(text):
    """正文页不能被误判成元信息页丢掉 —— 误杀的代价是**静默少一页内容**。"""
    assert not _SCANNER_META.search(text)


# ─── Ω 被 OCR 认成 Q(同一轮审计发现)────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("R=10Q", "R=10Ω"),
    ("j10Q", "j10Ω"),
    ("-j2.5Q", "-j2.5Ω"),
    ("Z=5kQ", "Z=5kΩ"),
    ("4+j3Q", "4+j3Ω"),
    ("C=10μQ", "C=10μΩ"),
    ("|Z|=20Q)", "|Z|=20Ω)"),        # 后面跟标点
    ("2Q熔断器", "2Ω熔断器"),         # 实测:数字+Q+汉字**全是 Ω**(37 处无一例外)
    ("10kQ的电阻", "10kΩ的电阻"),
    ("5kQ时", "5kΩ时"),
])
def test_ohm_symbol_is_restored_when_ocr_read_it_as_q(raw, expect):
    """Ω 有约四成被认成字母 Q:学生搜"10Ω"时 BM25 对不上正文里的"10Q"。

    这是**纯检索层**的损失 —— 内容明明在库里,只是词形对不上,所以召不回来。
    """
    fixed, n = _fix_ocr_symbols(raw)
    assert fixed == expect
    assert n == 1


@pytest.mark.parametrize("text", [
    "三极管 Q1",             # 后面是数字
    "10Quotient",            # 后面是字母
    "Q=10",                  # 前面不是数字
    "品质因数Q",             # 前面不是数字
    "第 Q 章",
    "Q点(静态工作点)",       # 前面不是数字
    "求 Q值",                # 前面不是数字(实测 14 处 Q值 全属这种)
    "2Q点",                  # 数字+Q,但后面是"点" → 显式排除
    "3Q值",                  # 同上(库里其实没有,防的是将来)
    "4Q参数",
])
def test_ohm_fix_never_touches_a_real_letter_q(text):
    """护栏必须拦住真正该是字母 Q 的地方。

    误改比不改更糟:改错之后正文**看起来是对的**(一个通顺的 Ω),
    学生和助教都不会去核对。
    """
    fixed, n = _fix_ocr_symbols(text)
    assert fixed == text, f"误改了:{text!r} → {fixed!r}"
    assert n == 0


def test_ohm_fix_is_idempotent():
    """脚本会被反复重跑 —— 第二次不能把已经改好的 Ω 再动一次。"""
    once, _ = _fix_ocr_symbols("R=10Q 与 20Ω 串联")
    twice, n2 = _fix_ocr_symbols(once)
    assert once == twice == "R=10Ω 与 20Ω 串联"
    assert n2 == 0, "重跑时不该再有可改的地方"
