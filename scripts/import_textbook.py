#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 OCR 好的**扫描版教材**装配成可检索语料:一章一个文件 + 印刷页号标记。

三个要点(改之前先读):

1. **印刷页码 ≠ PDF 页码,差 16 页。** 书前有封面/版权/译者序/前言/作者简介/目录,
   共 16 页(PDF 1~16,用罗马数字页码)。实测 PDF 19/20/21 页的书眉分别印着 3/4/5,
   所以 `印刷页 = PDF 页 - 16`。本脚本会**逐页用书眉自查**这个偏移,不一致的页会报出来。
   为什么要死抠这个:错误集锦里写的是**书上页码**("P9 应为 115 V""P188 这题不用做"),
   答学生"教材 P188"时必须对得上,否则指向错误。

2. **目录页(PDF 13~16)不进语料。** 它列着全部章节名和页码 —— 正是
   `docs/knowledge_base.md` §3.4.1 说的那种"谁提问它都高分"的清单,会挤掉真内容。
   同理封面、版权页、作者简介也不进。

3. **每页开头插 `[教材 P{印刷页}]` 标记。** 这样大模型能答"教材 P188 这道题印错了",
   学生翻书能直接对上。这是它比讲义(PPT 导出,没有页码)强的地方。

4. **文件名必须带 `教材-` 前缀**(见 `_FILE_PREFIX`)。`讲义/` 里的文件名格式一模一样
   (`第1章-基本概念.md`、`第4章-电路定理.md`…与教材章名逐字相同),而索引里的 `source`
   只存**文件名**不含目录 —— 不加前缀,学生看到的「参考:第4章-电路定理.md」就分不出
   那是课堂 PPT 还是教材正文。

5. **丢掉扫描仪的元信息页**(见 `_SCANNER_META`)。扫描件最后一页常是超星导出时附带的
   "书名=… 页数=693 SS号=…"页,它**不是书的内容**,却被当成正文打上了页码标记进了索引。

6. **把被认成字母 Q 的 Ω 改回来**(见 `_OHM_OCR`)。OCR 把约四成的 Ω 认成了 Q,
   学生搜"10Ω"时 BM25 对不上正文里的"10Q",那块内容就召不回来。

用法:
    python scripts/import_textbook.py --dry-run     # 看要写哪些文件、书眉页码对不对
    python scripts/import_textbook.py               # 落盘
    python scripts/import_textbook.py --report-sections   # 诊断:正文自动抽的节标题(噪声大)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402
from src.utils.stderr_log import warn as _warn  # noqa: E402

COURSE = "电路基础"
TEACHER = "张曰理"
BOOK = "《电路基础》(原书第 6 版,中文版)"

DEFAULT_CACHE = Path("/home/jj/ykt_questions/_ocr_cache/电路基础-6th")
SRC_NAME = "电路基础-中文版-原书第6版（扫描版）.pdf"

# 印刷页 = PDF 页 - 本值(见文件头说明;脚本会用书眉逐页自查)
PDF_TO_PRINTED = 16

# 书前 16 页(封面/版权/译者序/前言/作者简介/目录)。**其中目录页尤其不能进语料**(见要点 2)。
FRONT_MATTER_LAST_PDF = 16

# 文件名前缀。**必须有**:`讲义/` 里的文件名格式一模一样
# (`第1章-基本概念.md`、`第4章-电路定理.md`…与教材章名逐字相同),
# 而索引里的 `source` 只存**文件名**不含目录 —— 不加前缀的话,学生看到
# 「参考:第4章-电路定理.md」根本分不出那是课堂 PPT 还是教材正文。
_FILE_PREFIX = "教材-"

# 译者序里有一段"本教材符号与国标不同"的说明(导线交叉处**有没有黑点**的含义与国标
# 相反),学生照着国标读图会读错,单独留一份。
#
# **只收 PDF 5。** 实测:PDF 4 是「出版者的话」(讲这套丛书的历史,与电路无关),
# PDF 5 才是「译者序」,符号那段在 p0005 里(含 5 处"国标/符号")。
# 早先误写成 (4, 5),产出物开头是"出版者的话"却在标题里自称"符号与国标差异" ——
# 一个**自称讲 A 实际是 B** 的文件比没有文件更糟,大模型会拿它去答符号问题。
TRANSLATOR_NOTE_PDFS = (5,)

# 章:印刷起始页 → (章号, 章名)。取自书的目录,人工核对过。
CHAPTERS: list[tuple[int, int, str]] = [
    (2, 1, "基本概念"),
    (21, 2, "基本定律"),
    (59, 3, "分析方法"),
    (96, 4, "电路定理"),
    (133, 5, "运算放大器"),
    (162, 6, "电容与电感"),
    (191, 7, "一阶电路"),
    (235, 8, "二阶电路"),
    (278, 9, "正弦量与相量"),
    (310, 10, "正弦稳态分析"),
    (340, 11, "交流功率分析"),
    (372, 12, "三相电路"),
    (410, 13, "磁耦合电路"),
    (452, 14, "频率响应"),
    (500, 15, "拉普拉斯变换简介"),
    (528, 16, "拉普拉斯变换的应用"),
    (561, 17, "傅里叶级数"),
    (601, 18, "傅里叶变换"),
    (628, 19, "二端口网络"),
]
APPENDIX_START = 670          # 附录(奇数编号习题答案等)

# 每章的**概念词**(锚点用)。逐条抄自书的目录,人工核对过。
#
# 为什么不用 `--report-sections` 自动抽:实测噪声太大 —— 正文里公式行、图注也长成
# "3.4 xxx" 的样子,抽出来的"节标题"有 `1.6 X10- = 25 000(V) 4` 这种;
# 而且**真的节会漏**(1.8 解题方法、3.6、3.9 都没抽到)。锚点是给检索用的,
# 宁缺毋滥 —— 写错的词会把无关提问吸过来。
#
# 这里只列**学生真会问出口的概念名**(定理名、元件名、方法名),不列
# "基于PSpice的电路分析""本章小结"这类结构性标题 —— 它们没有检索价值。
CONCEPTS: dict[int, list[str]] = {
    1: ["计量单位制", "电荷与电流", "电压", "功率与能量", "电路元件", "解题方法"],
    2: ["欧姆定律", "节点", "支路", "回路", "基尔霍夫定律", "KCL", "KVL",
        "串联电阻分压", "并联电阻分流", "Y-△变换"],
    3: ["节点分析法", "网孔分析法", "观察法", "含有电压源的节点分析",
        "含有电流源的网孔分析"],
    4: ["线性性质", "叠加定理", "电源变换", "戴维南定理", "诺顿定理", "最大功率传输定理"],
    5: ["运算放大器", "理想运算放大器", "虚短虚断", "反相放大器", "同相放大器",
        "加法放大器", "差分放大器", "级联电路"],
    6: ["电容", "电感", "电容的串并联", "电感的串并联", "储能元件"],
    7: ["无源RC电路", "无源RL电路", "奇异函数", "阶跃响应", "时间常数",
        "一阶运算放大器电路"],
    8: ["初值和终值", "无源串联RLC电路", "无源并联RLC电路", "串联RLC阶跃响应",
        "并联RLC阶跃响应", "一般二阶电路", "对偶原理"],
    9: ["正弦信号", "相量", "电路元件的相量关系", "阻抗与导纳", "频域中的基尔霍夫定律",
        "阻抗合并"],
    10: ["正弦稳态分析", "节点分析法", "网孔分析法", "叠加定理", "电源变换",
         "戴维南等效电路", "诺顿等效电路", "交流运算放大器电路"],
    11: ["瞬时功率", "平均功率", "最大平均功率传输", "有效值", "方均根值",
         "视在功率", "功率因数", "复功率", "交流功率守恒", "功率因数的校正"],
    12: ["三相电路", "对称三相电压", "对称Y-Y联结", "对称Y-△联结", "对称△-△联结",
         "对称△-Y联结", "对称系统中的功率", "非对称三相系统"],
    13: ["磁耦合电路", "互感", "耦合电路中的能量", "线性变压器", "理想变压器",
         "理想自耦变压器", "三相变压器"],
    14: ["频率响应", "传递函数", "分贝表示法", "伯德图", "串联谐振电路",
         "并联谐振电路", "无源滤波器", "有源滤波器", "比例转换"],
    15: ["拉普拉斯变换", "拉普拉斯变换的定义", "拉普拉斯变换的性质",
         "拉普拉斯反变换", "卷积积分"],
    16: ["拉普拉斯变换的应用", "电路元件的s域模型", "传递函数", "状态变量"],
    17: ["傅里叶级数", "三角函数形式的傅里叶级数", "频谱分析", "平均功率与方均根值",
         "指数形式的傅里叶级数"],
    18: ["傅里叶变换", "傅里叶变换的定义", "傅里叶变换的性质", "帕塞瓦尔定理",
         "傅里叶变换和拉普拉斯变换的比较"],
    19: ["二端口网络", "阻抗参数", "导纳参数", "混合参数", "传输参数",
         "六组参数之间的关系", "二端口网络的互联"],
}

_EXTRA_KW = ("教材 课本 教科书 中文版 第六版 第6版 定义 概念 原理 定理 公式 推导 "
             "讲解 详解 怎么理解 是什么 为什么")

# 节标题:行首 "4.3叠加定理" / "4.3 叠加定理"。章号必须对得上,标题不能以数字开头
# (否则会把 "3.4 20log..." 这种公式行抓进来)。
_SEC_LINE = re.compile(r"^\s*(\d{1,2})\s*[.．]\s*(\d{1,2})\s*([^\d\s].{0,20}?)\s*$")
# 书眉:如 "第1章基本概念3"、"4第一部分直流电路"、"第10章正弦稳态分析315"
_HEADER = re.compile(r"^\s*(?:第\s*(\d{1,2})\s*章|(\d{1,2})\s*第([一二三])部分).*?(\d{1,4})\s*$")

# 扫描仪/超星导出的**元信息页**:`[ Gener al I nf or nat i on] 书名=… 页数=693 SS号=…`
# 它附在扫描件最后一页,**不是书的内容**,但会被当成正文打上 `[教材 P695]` 进索引。
# (2026-09-20 语料审计发现:全库 grep `SS号|页数=` 只命中这一处。)
# 风险:学生问"教材最后附录里有什么"时,模型可能把"页数=693 SS号=14536644"当正文讲。
# 匹配要容忍 OCR 在英文单词里塞的空格,所以写成 `Gener\s*al\s*I\s*nf` 而不是原样字面量。
_SCANNER_META = re.compile(r"SS\s*号\s*=|Gener\s*al\s*I\s*nf|页数\s*=\s*\d+")

# Ω 被 OCR 认成字母 Q。审计实测:数字紧邻的 Ω 有 1942 处、Q 有 1238 处 ——
# 也就是**约四成的欧姆符号是错的**。后果是纯检索层的:学生问「10Ω 的电阻在哪讲过」,
# BM25 词形匹配不到正文里的「10Q」,那块内容就召不回来。
#
# 只在**数字(可带 k/M/m/μ 等数量级前缀)紧邻 Q** 时才改。护栏该放多宽是**量出来的**
# (读 711 页 OCR 缓存数了一遍):数字紧邻的 Q 共 1959 处,其中
#   · Q 后面是空格/换行/标点的 1922 处;
#   · Q 后面是汉字的 37 处 —— 而这 37 处**全是 Ω**
#     (`6Q电阻`、`18kQ电阻`、`2kQ的达松伐尔电表`、`5kQ时`);
#   · 真该是字母 Q 的词里,`Q点`/`Q参数`/`静态工作点` 在库里出现 **0 次**,
#     `Q值` 有 14 次但**前面都是汉字或行首**,没有一次紧跟数字。
# 所以护栏按实测放宽到"CJK 一律允许",只显式排除 `Q值/Q点/Q参数/Q因数` 的首字 ——
# 挡的是**将来**的编辑(有人往正文里写"2Q点"),不是今天的数据。
# "Q 后面不能是字母/数字"那条护栏保留:`Q1`(三极管编号)、`10Quotient` 不能被改。
_OHM_OCR = re.compile(r"(\d(?:[kKmMμnGgTt])?)Q(?![0-9A-Za-z值点参因品])")


def _load_pages(cache: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for f in sorted(cache.glob("p*.txt")):
        try:
            n = int(f.stem[1:])
        except ValueError:
            continue
        out[n] = f.read_text(encoding="utf-8")
    return out


def _strip_running_header(text: str) -> str:
    """去掉每页第一行的书眉。

    书眉("第1章基本概念3")在**每一页**重复出现,会在章内平白堆高这些词的词频;
    而且章名已经写在锚点里了,书眉里的页码也已经由 `[教材 P{n}]` 标记承担。
    只在"短、且带 章/部分"时才删 —— 章首页的书眉下面常常紧跟着正文,别误删。
    """
    lines = text.split("\n")
    if lines and len(lines[0]) <= 30 and re.search(r"第\s*\d{0,2}\s*(?:章|部分)|第[一二三]部分", lines[0]):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _page_ok(pdf_no: int, text: str) -> tuple[bool, str]:
    """用书眉自查页码偏移:书眉里的数字应当等于 印刷页 = pdf - 16。"""
    expect = pdf_no - PDF_TO_PRINTED
    for ln in text.split("\n")[:3]:
        m = _HEADER.match(ln)
        if m and m.group(4):
            got = int(m.group(4))
            return (got == expect, f"书眉 {got} vs 期望 {expect}")
    return (True, "无书眉(不判)")


def _harvest_sections(pages: dict[int, str], ch: int, lo: int, hi: int) -> list[str]:
    seen: list[str] = []
    for p in range(lo, hi + 1):
        t = pages.get(p + PDF_TO_PRINTED)
        if not t:
            continue
        for ln in t.split("\n"):
            m = _SEC_LINE.match(ln)
            if not m or int(m.group(1)) != ch:
                continue
            title = re.sub(r"[\s.·…]+$", "", m.group(3)).strip()
            key = f"{int(m.group(1))}.{int(m.group(2))} {title}"
            if title and key not in seen:
                seen.append(key)
    return seen


def _chapter_header(ch: int, title: str, lo: int, hi: int, secs: list[str]) -> str:
    kw: list[str] = []
    for x in ([COURSE, "教材", "课本", "中文版", "第六版", "第6版",
               f"第{ch}章", f"CH{ch}", title] + secs + [_EXTRA_KW]):
        for w in (x.split() if isinstance(x, str) else [x]):
            if w and w not in kw:
                kw.append(w)
    return (
        f"【{COURSE} 教材(中文版第6版) 第{ch}章 {title}】{BOOK}\n"
        f"来源:{SRC_NAME} 印刷页 P{lo}-P{hi}(扫描件经 OCR,公式/上下标可能有乱码,以书为准)\n"
        f"检索:{' '.join(kw)}\n"
    )


def _appendix_header(lo: int, hi: int) -> str:
    return (
        f"【{COURSE} 教材(中文版第6版) 附录 奇数编号习题答案】{BOOK}\n"
        f"来源:{SRC_NAME} 印刷页 P{lo}-P{hi}\n"
        f"⚠️ 这里只有**最终答案**,没有解题过程。本项目作业题的权威解答见各章题目文件"
        f"(中文版作业用书)。\n"
        f"检索:{COURSE} 教材 附录 答案 习题答案 奇数编号 习题 自测 对答案 核对 "
        f"最终结果 参考答案\n"
    )


def _translator_header() -> str:
    return (
        f"【{COURSE} 教材(中文版第6版) 译者序:符号与国标差异说明】{BOOK}\n"
        f"来源:{SRC_NAME} 书前页(罗马数字页码,无阿拉伯页码)\n"
        f"检索:{COURSE} 教材 译者序 符号 约定 国标 导线 交叉 连接 黑点 电容 电感 "
        f"电压源 电流源 受控源 运算放大器 变压器 画法 不一样 怎么读图\n"
    )


def _translator_note(pages: dict[int, str]) -> str:
    """译者序里的符号约定。

    **书前页不加 `[教材 P{n}]` 标记**:书前 16 页用的是罗马数字页码,套
    "印刷页 = PDF 页 - 16" 会算出 `P-12` 这种**负页码**(实测踩到过),
    学生按它翻书永远翻不到。正文才用阿拉伯页码,所以标记只给正文。
    """
    note_pages = {p: pages[p] for p in TRANSLATOR_NOTE_PDFS if p in pages}
    if not note_pages:
        return ""
    parts = [_translator_header()]
    for p, t in sorted(note_pages.items()):
        if p - PDF_TO_PRINTED < 1:
            _warn(f"[textbook] 译者序取自书前第 {p} 页(PDF),按惯例不加页码标记")
        parts.append(f"\n{t.strip()}\n")
    return "".join(parts)


def _fix_ocr_symbols(text: str) -> tuple[str, int]:
    """把被 OCR 认成字母 Q 的 Ω 改回来(见 `_OHM_OCR`),返回(新文本, 改了几处)。"""
    return _OHM_OCR.subn(r"\1Ω", text)


def _render(header: str, pages: dict[int, str], lo: int, hi: int
            ) -> tuple[str, list[str], list[str]]:
    """把 [lo, hi](印刷页,闭区间)的页拼成正文,每页前加 `[教材 P{n}]` 标记。

    返回 `(正文, 书眉对不上的页, 跳过的页)`。
    """
    parts = [header]
    bad: list[str] = []
    skipped: list[str] = []
    for pr in range(lo, hi + 1):
        pdf_no = pr + PDF_TO_PRINTED
        t = pages.get(pdf_no)
        if not t or not t.strip():
            continue
        body = _strip_running_header(t).strip()
        # 扫描仪元信息页**整页丢掉**:留着比没有更糟 —— 它会带着 `[教材 P695]`
        # 混进索引,模型于是可以拿"页数=693 SS号=14536644"当教材正文讲给学生。
        if _SCANNER_META.search(body):
            skipped.append(f"PDF{pdf_no}(印刷{pr})")
            continue
        ok, why = _page_ok(pdf_no, t)
        if not ok:
            bad.append(f"PDF{pdf_no}(印刷{pr}):{why}")
        parts.append(f"\n[教材 P{pr}]\n{body}\n")
    return "".join(parts), bad, skipped


def build(cache: Path, out: Path, dry_run: bool, report_sections: bool) -> dict:
    pages = _load_pages(cache)
    if not pages:
        _warn(f"[textbook] ⚠️ OCR 缓存是空的:{cache}")
        return {"pages": 0}
    last_pdf = max(pages)
    last_printed = last_pdf - PDF_TO_PRINTED
    _warn(f"[textbook] OCR 缓存 {len(pages)} 页,最大 PDF 页 {last_pdf}(印刷 {last_printed})")
    missing = [p for p in range(1, last_pdf + 1) if p not in pages]
    if missing:
        _warn(f"[textbook] ⚠️ 缺 {len(missing)} 页,例如 {missing[:12]} —— 正文会有洞")

    ends = [c[0] for c in CHAPTERS[1:]] + [APPENDIX_START]
    stats = {"chapters": 0, "appendix": 0, "translator": 0, "chars": 0,
             "bad_pages": [], "skipped_pages": [], "ohm_fixes": 0, "sections": {}}

    if report_sections:
        _warn("[textbook] ── 抽到的节标题(人工过一遍,把真的填进 SECTIONS)──")
        for (lo, ch, title), hi in zip(CHAPTERS, ends):
            secs = _harvest_sections(pages, ch, lo, hi - 1)
            _warn(f"[textbook] 第{ch}章 {title}(P{lo}-P{hi - 1}):{len(secs)} 节")
            for s in secs:
                _warn(f"[textbook]     {s}")
            stats["sections"][ch] = secs
        return stats

    dest_dir = out / "教材"
    for (lo, ch, title), hi in zip(CHAPTERS, ends):
        secs = CONCEPTS.get(ch, [])
        text, bad, skipped = _render(_chapter_header(ch, title, lo, hi - 1, secs),
                                     pages, lo, hi - 1)
        text, n_ohm = _fix_ocr_symbols(text)
        stats["bad_pages"] += bad
        stats["skipped_pages"] += skipped
        stats["ohm_fixes"] += n_ohm
        if len(text) < 500:
            _warn(f"[textbook] ⚠️ 第{ch}章只有 {len(text)} 字符,跳过")
            continue
        stats["chars"] += len(text)
        stats["chapters"] += 1
        if not dry_run:
            _write(dest_dir / f"{_FILE_PREFIX}第{ch}章-{title}.md", text)

    # 附录:从 APPENDIX_START 到书末
    if last_printed > APPENDIX_START:
        text, bad, skipped = _render(_appendix_header(APPENDIX_START, last_printed),
                                     pages, APPENDIX_START, last_printed)
        text, n_ohm = _fix_ocr_symbols(text)
        stats["bad_pages"] += bad
        stats["skipped_pages"] += skipped
        stats["ohm_fixes"] += n_ohm
        stats["chars"] += len(text)
        stats["appendix"] = 1
        if not dry_run:
            _write(dest_dir / f"{_FILE_PREFIX}附录-奇数编号习题答案"
                              f"(P{APPENDIX_START}-P{last_printed}).md", text)

    # 译者序里的符号约定
    text = _translator_note(pages)
    if text:
        stats["translator"] = 1
        stats["chars"] += len(text)
        if not dry_run:
            _write(dest_dir / f"{_FILE_PREFIX}译者序-符号与国标差异.md", text)

    if not dry_run:
        (out / "_manifest_textbook.json").write_text(json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "cache_dir": str(cache),
            "output_dir": str(dest_dir),
            "pdf_to_printed_offset": PDF_TO_PRINTED,
            "pages_in_cache": len(pages),
            "chapters": stats["chapters"],
            "appendix": stats["appendix"],
            "translator_note": stats["translator"],
            "total_chars": stats["chars"],
            "bad_page_headers": stats["bad_pages"][:50],
            "skipped_scanner_pages": stats["skipped_pages"],
            "ohm_symbol_fixes": stats["ohm_fixes"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def _write(dest: Path, text: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--out", type=Path, default=None, help="默认 MATERIALS_DIR/电路基础/")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report-sections", action="store_true",
                    help="诊断:打印从正文自动抽到的节标题(噪声大,仅用于核对 CONCEPTS 有没有漏)")
    args = ap.parse_args()

    out = args.out or (config.MATERIALS_DIR / COURSE)
    if not args.cache.is_dir():
        _warn(f"[textbook] 缓存目录不存在:{args.cache}")
        return 1

    stats = build(args.cache, out, args.dry_run, args.report_sections)
    if args.report_sections:
        return 0
    _warn(f"[textbook] 章 {stats['chapters']} 篇 + 附录 {stats['appendix']} 篇 + "
          f"译者序 {stats['translator']} 篇,共 {stats['chars']} 字符"
          + ("(dry-run,未写盘)" if args.dry_run else f" → {out / '教材'}"))
    if stats["ohm_fixes"]:
        _warn(f"[textbook] 修正被 OCR 认成字母 Q 的 Ω:{stats['ohm_fixes']} 处")
    if stats["skipped_pages"]:
        _warn(f"[textbook] 跳过的扫描仪元信息页:{stats['skipped_pages']}")
    if stats["bad_pages"]:
        _warn(f"[textbook] ⚠️ 书眉页码与推断的偏移对不上 {len(stats['bad_pages'])} 页"
              f"(多半是章首页/整页图):{stats['bad_pages'][:8]}")
    if not args.dry_run:
        _warn("[textbook] 下一步:重建索引 python scripts/build_index.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
