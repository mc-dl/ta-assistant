#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「电路基础」课后作业答案整理成可被 BM25 检索的中文语料。

为什么要这个脚本(而不是直接把 PDF 丢进 MATERIALS_DIR 让 doc_parser 解析):
    1. 原始答案是**英文**的(Alexander & Sadiku 6th ed. 习题解答),
       而学生提问是**中文**("10.46 那题怎么做")。jieba 切中文、切英文是两套词,
       英文正文里没有「第十章」「作业」「答案」这些词,直接索引的话中文问题
       根本召不回对应片段。所以这里给每份解答**加一段中文标题头**当"锚"。
    2. 原始目录里混着三类不该进知识库的文件(见 SKIP_RULES),脚本里显式排除,
       并在末尾打印原因,免得下次有人一股脑全塞进去。
    3. 一次生成、可重复运行:源文件更新了重跑一次即可,不用手工搬运。

用法(WSL 内):
    python scripts/import_circuit_basic.py                    # 用默认源目录
    python scripts/import_circuit_basic.py --src /path/to/dir
    python scripts/import_circuit_basic.py --dry-run          # 只看会发生什么
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.utils.stderr_log import warn as _warn  # noqa: E402

# 源目录默认值:答案文件现在放在 Windows 侧 Downloads 下,WSL 通过 /mnt/c 读
DEFAULT_SRC = Path("/mnt/c/Users/52880/Downloads/电路基础课后作业答案")

COURSE = "电路基础"
TEACHER = "张曰理"
TEXTBOOK = "Alexander & Sadiku《Fundamentals of Electric Circuits》第6版(2017, McGraw-Hill)"

# 版权页样板文字:每份 Soln 末尾都有,提示词和检索都不需要,去掉省 token。
# **两种写法都出现过**(实测):
#   ① "Copyright © 2017 McGraw-Hill Education. All rights reserved. ... written consent of ..."
#   ② " 2017 McGraw-Hill Education. All rights reserved. No reproduction or distribution
#       without the prior written consent of McGraw-Hill Education."
#
# 为什么是"找起点、砍掉尾巴"而不是写一条完整的匹配正则:
# 一开始写成 `^\s*\d{4}\s+McGraw-Hill Education\..*?(?:written consent...|$)` 配 re.M,
# 想用 `$` 兜底"万一没有 written consent 那句";结果 `.*?` 是非贪婪的,
# 每扩张一个字符就试一次 `$`,在**第一行行尾**就命中了,于是只删掉一行、
# 把 "prior written consent of McGraw-Hill Education." 留了下来(实测 65 份全中招)。
# 样板总在文末,所以直接定位起点、砍掉尾部更简单也更准。
_COPYRIGHT_START = re.compile(
    r"(?:\n|^)[ \t]*(?:Copyright\s*©|©\s*\d{4}|(?:\d{4}\s+)?McGraw-Hill Education\.)"
)
# 样板长度上限:超过了就说明匹配到的是正文里偶然出现的字样,不能整段砍
_COPYRIGHT_MAX_TAIL = 400

# **完整**的样板(带收尾句)。多页解答的**每页页脚**都印一份,所以要全文替换,
# 不能只砍文末那一段 —— 实测 65 份里有 14 份是两页以上,第一份页脚就落在正文中间。
_COPYRIGHT_BLOCKS = (
    re.compile(r"Copyright\s*©.{0,300}?written consent of McGraw-Hill Education\.", re.S),
    re.compile(r"\d{4}\s+McGraw-Hill Education\..{0,300}?written consent of McGraw-Hill Education\.",
               re.S),
)


def strip_copyright(text: str) -> str:
    """删掉 McGraw-Hill 版权样板(所有页脚 + 文末残留)。"""
    prev = None
    while prev != text:
        prev = text
        for pat in _COPYRIGHT_BLOCKS:
            text = pat.sub("", text)
    # 兜底:有些页脚被排版截断,没有收尾句,上面两条都匹配不到。
    # 此时若"版权起点"距文末很近,就直接砍掉尾巴。
    last = None
    for m in _COPYRIGHT_START.finditer(text):
        last = m
    if last is not None and len(text) - last.start() <= _COPYRIGHT_MAX_TAIL:
        text = text[: last.start()]
    return _clean(text)

# Soln10046.pdf → 章节号取文件名前 1~2 位、题号两位;但**优先用正文里的
# "Solution 10.46" 兜底**,因为文件名切分天然有歧义(01023 可能是 1.23 也可能是 01.023)
_SOLN_NAME = re.compile(r"^Soln(\d+)\.pdf$", re.I)
_SOLUTION_HEAD = re.compile(r"Solution\s+(\d{1,2})\.(\d{1,3})", re.I)
# 章节目录名,如 "10第十章" / "1第一章"
_CHAPTER_DIR = re.compile(r"^(\d{1,2})(第.+章)$")
# 参考答案 docx 里的分节:行首 "2." / "3、" / 或 "书10.60"
_DOCX_SPLIT = re.compile(r"(?m)^\s*(?:(\d{1,2})\s*[.、]|书\s*(\d{1,2}\.\d{1,3}))")


# 目录里几份"看着像资料、其实不能进知识库"的文件,逐个写明为什么。
# (2026-09-20 逐个实测过,不是拍脑袋)
KNOWN_SKIP: dict[str, str] = {
    "电路基础-中文版-原书第6版（扫描版）.pdf":
        "纯扫描件,无文本层(711 页只能提取 112 字符),需 OCR 才能用,排除",
    "Fundamentals of Electric Circuits (4th Ed.) Solution Manual (Charles K. Alexander, Matthew N. O. Sadiku) (Z-Library).pdf":
        "第 4 版答案手册:与作业所用第 6 版**版次不同**,题号会串(实测搜不到 'Solution 10.46'),照它答会答错题,排除",
    "Fundamentals of Electric Circuits (4th Ed.) Solutions Manual (Charles K. Alexander Matthew N.O. Sadiku).pdf":
        "同上(重复副本),排除",
    "Fundamentals Of Electric Circuits (Charles K. Alexander  Matthew N. O. Sadiku) (Z-Library).pdf":
        "英文教材正文 993 页/1.7M 字符:对中文提问几乎无召回,还会稀释 BM25 结果,暂不索引",
}


def _clean(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_text(path: Path) -> str:
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for pg in pdf.pages:
            t = pg.extract_text() or ""
            if t.strip():
                parts.append(t)
    return _clean("\n".join(parts))


def _docx_text(path: Path) -> str:
    """只取 <w:t> 文本节点。

    为什么不用 python-docx 的 paragraphs:这些 docx 里的公式/图形是画布对象,
    正文文字在 <w:txbxContent> 里,python-docx 读不到;而直接对 document.xml
    去标签又会把 <wp:posOffset>296640</wp:posOffset> 这类**排版坐标数字**当成
    正文掺进来(实测能掺出几十位数字串)。只认 <w:t> 就干净了。
    """
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n")
    out: list[str] = []
    for m in re.finditer(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|(\n)", xml, re.S):
        out.append(m.group(1) if m.group(1) is not None else "\n")
    text = "".join(out)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(a, b)
    return _clean(text)


def _split_docx_sections(text: str) -> list[tuple[str, str]]:
    """把一个参考答案 docx 拆成 [(小节标签, 内容), ...]。"""
    marks = list(_DOCX_SPLIT.finditer(text))
    if not marks:
        return [("全文", text)]
    sections: list[tuple[str, str]] = []
    # 开头若有前言/标题,单独成节
    if marks[0].start() > 0:
        head = text[: marks[0].start()].strip()
        if head:
            sections.append(("前言", head))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.start(): end].strip()
        if body:
            label = f"书{m.group(2)}" if m.group(2) else f"第{m.group(1)}题"
            sections.append((label, body))
    return sections


def _problem_ref(pdf: Path, body: str) -> tuple[str, str, str]:
    """推 (章节号, 题号, 来源标记)。

    **优先看正文里的 "Solution X.Y"**,这很重要:文件名 `Soln11012.pdf` 里那个
    012 并不是题号(它对应的正文是 "Solution 11.2"),照文件名切会张冠李戴。
    正文自述才是真的。文件名只在正文缺头时兜底。
    """
    m = _SOLUTION_HEAD.search(body[:120]) or _SOLUTION_HEAD.search(body)
    if m:
        return str(int(m.group(1))), f"{int(m.group(1))}.{m.group(2)}", "正文"
    m = _SOLN_NAME.match(pdf.name)
    if m:
        digits = m.group(1)
        if len(digits) >= 3:
            return str(int(digits[:-3] or 0)), f"{int(digits[:-3] or 0)}.{int(digits[-3:])}", "文件名(兜底)"
    return "", pdf.stem, "无题号"


def _header(ch: str, prob: str, chdir: str | None, src_name: str, extra: str = "") -> str:
    """标题头 —— 中文锚点。

    这三行是给 **BM25 用的**:正文是英文,中文提问检索不到,所以把
    「电路基础/作业/答案/第X章/题号」这些学生真会打的词显式写进来。
    """
    where = f"第{ch}章" if ch else (chdir or "")
    kw: list[str] = []
    for x in ["电路基础", "作业", "答案", "习题", "解答", "参考",
              where, f"第{ch}章" if ch else "", f"CH{ch}" if ch else "",
              prob, f"题{prob}" if prob else "", extra]:
        if x and x not in kw:
            kw.append(x)
    return (
        f"【{COURSE} {where} 习题 {prob} 参考答案】任课老师:{TEACHER}\n"
        f"来源:{src_name}({TEXTBOOK})\n"
        f"检索:{' '.join(kw)}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=None,
                    help="默认落到 MATERIALS_DIR/电路基础/")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src: Path = args.src
    out: Path = args.out or (config.MATERIALS_DIR / COURSE)
    if not src.exists():
        _warn(f"[import] 源目录不存在:{src}")
        return 1

    _warn(f"[import] 源目录:{src}")
    _warn(f"[import] 输出到:{out}")
    ("" if args.dry_run else out.mkdir(parents=True, exist_ok=True))

    written: list[dict] = []
    skipped: list[tuple[str, str]] = []

    # ── 1) 每章的单题解答 SolnNNNNN.pdf ────────────────────────────
    for sub in sorted(p for p in src.iterdir() if p.is_dir()):
        m = _CHAPTER_DIR.match(sub.name)
        if not m:
            continue
        ch_from_dir, chapter_name = str(int(m.group(1))), m.group(2)
        for pdf in sorted(sub.glob("*.pdf")):
            body = _pdf_text(pdf)
            if len(body) < 60:
                skipped.append((str(pdf.relative_to(src)), f"可提取文本过少({len(body)} chars),疑似扫描件"))
                continue
            body = strip_copyright(body)
            ch, prob, how = _problem_ref(pdf, body)
            if not ch:
                ch = ch_from_dir
            if ch != ch_from_dir:
                _warn(f"[import] ⚠️ 题号来源不一致:{pdf.name} 在目录 {chapter_name},"
                      f"但正文说第 {ch} 章(采用正文)")
            text = _header(ch, prob, chapter_name, pdf.name) + "\n" + body + "\n"
            dest = out / sub.name / f"{prob}.md"
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            written.append({"out": str(dest.relative_to(out)), "src": pdf.name,
                            "kind": "soln_pdf", "number_source": how, "chars": len(body)})

    # ── 2) 中文参考答案 docx(按小节拆) ────────────────────────────
    for docx in sorted(src.glob("*.docx")):
        if docx.name.startswith("~$"):
            continue
        text = _docx_text(docx)
        if len(text) < 60:
            skipped.append((docx.name, f"可提取文本过少({len(text)} chars)"))
            continue
        for i, (label, body) in enumerate(_split_docx_sections(text), 1):
            head = (
                f"【{COURSE} 作业参考答案(中文) {label}】任课老师:{TEACHER}\n"
                f"来源:{docx.name}\n"
                f"检索:{COURSE} 作业 答案 解答 参考 中文 {label} 习题\n"
            )
            dest = out / "参考答案" / f"{docx.stem}_{i:02d}_{label}.md"
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(head + "\n" + body + "\n", encoding="utf-8")
            written.append({"out": str(dest.relative_to(out)), "src": docx.name,
                            "kind": "answer_docx", "chars": len(body)})

    # ── 3) 显式排除的文件(留个明白账) ─────────────────────────────
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(src))
        if p.name in KNOWN_SKIP:
            skipped.append((rel, KNOWN_SKIP[p.name]))
        elif p.suffix.lower() in (".jpg", ".png", ".jpeg", ".webp"):
            skipped.append((rel, "图片,当前 doc_parser 不支持 OCR,未索引"))
        elif p.suffix.lower() == ".pdf" and p.stat().st_size > 5_000_000:
            skipped.append((rel, f"{p.stat().st_size/1048576:.0f} MB,超过体积阈值,请人工确认后再决定是否索引"))
        elif p.suffix.lower() not in (".pdf", ".docx"):
            skipped.append((rel, "非 pdf/docx,未处理"))

    # ── 4) 目录清单(方便「某章有哪些题」这类问题召回) ──────────────
    toc_lines = [
        f"# {COURSE} 课后作业答案 目录",
        f"任课老师:{TEACHER}。以下为已整理进知识库的作业答案清单。",
        "",
    ]
    by_ch: dict[str, list[str]] = {}
    for w in written:
        if w["kind"] == "soln_pdf":
            parts = Path(w["out"]).parts
            by_ch.setdefault(parts[0], []).append(parts[-1].removesuffix(".md"))
    for chdir, probs in sorted(by_ch.items(), key=lambda kv: kv[0]):
        toc_lines.append(f"- {chdir}:{'、'.join(sorted(probs))}")
    toc_lines += ["", "另有中文参考答案(作业第2~7题)见 `参考答案/` 目录。", ""]
    toc_text = "\n".join(toc_lines)
    toc_dest = out / "00_作业答案目录.md"
    if not args.dry_run:
        toc_dest.write_text(toc_text, encoding="utf-8")

    # ── 5) 清单文件(可追溯) ───────────────────────────────────────
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "course": COURSE,
        "teacher": TEACHER,
        "source_dir": str(src),
        "output_dir": str(out),
        "written_count": len(written),
        "total_chars": sum(w["chars"] for w in written),
        "written": written,
        "skipped": [{"file": f, "reason": r} for f, r in skipped],
    }
    if not args.dry_run:
        (out / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 汇报 ──────────────────────────────────────────────────────
    _warn(f"[import] 生成 {len(written)} 个语料文件,共 {manifest['total_chars']:,} 字符")
    _warn(f"[import] 目录清单:{toc_dest.name}")
    _warn("[import] 排除的文件(原因逐条写在上面,不是按体积一刀切):")
    for f, r in skipped:
        _warn(f"    - {f}\n        ← {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
