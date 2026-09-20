#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「202509电路理论基础」整理成可检索的中文语料:中文版作业答案 + 张老师课件 + 错误集锦。

**为什么要有这个脚本**(2026-09-20 助教反馈):
    之前的知识库只有英文版习题解答(Alexander & Sadiku 第 6 版 Soln)。
    助教指出两个问题:
      ① **国际版和中文版的题目参数可能不同**(例如公里变成英里),
         所以**课后题答案要以中文版为准**;
      ② 知识库里只有作业答案、没有教材/课件,问「什么是叠加原理」时无据可依。
    张老师的课堂 PPT 正好补上 ② —— 那是**概念题的权威依据**。

**这个脚本做什么**:
    ① 中文版作业答案(`Z第N章 作业题目及答案.pdf`)→ 按题号切分,
       **写到和英文版同一个文件路径上**(同名即同题),也就是**覆盖**;
       英文原文被降级成文末的「英文版对照」,并注明数值以中文版为准。
       英文版**独有**的题目(中文作业没布置的,如第 13、19 章)**原样不动**。
    ② 课堂 PPT(`第N章-xxx.pdf`)→ 每章一篇,标题头写明"张曰理老师课件"。
    ③ 错误集锦(`电路理论基础---错误集锦*.pptx`)→ 一篇,教材勘误(含"此题不用做")。

⚠️ 本脚本只产出**原始语料**,不含大模型写的【中文详细解析】。默认**不覆盖**
   已有详细解析的文件(拿原始语料盖掉解析是倒退)。正确顺序是:

       import_circuit_theory.py   落盘(讲义/错误集锦 + 题目原始语料)
       enhance_circuit_basic.py   把题目文件升级成"中文版详细解析 + 英文版对照"
       build_index.py             重建索引

   那 53 道中英都有的题,**详细解析必须按中文版重写**(旧的解析照英文版原文写的,
   而国际版个别题参数不同)。这件事归增强脚本做 —— 它直接从源 PDF 重抽中文原文,
   不读本脚本写的文件。

用法(WSL 内):
    python scripts/import_circuit_theory.py --dry-run   # 只看切出哪些题,不写盘
    python scripts/import_circuit_theory.py             # 落盘(跳过已有详细解析的)
    python scripts/import_circuit_theory.py --force     # 连详细解析的文件也一起覆盖
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_circuit_basic as base  # noqa: E402
from src import config  # noqa: E402
from src.utils.stderr_log import warn as _warn  # noqa: E402

# 源目录:张老师的课件 + 中文版作业答案(在 Windows 侧 Downloads 下,WSL 通过 /mnt/c 读)
DEFAULT_SRC = Path("/mnt/c/Users/52880/Downloads/202509电路理论基础")

COURSE = base.COURSE          # "电路基础"
TEACHER = base.TEACHER        # "张曰理"
TEXTBOOK_CN = "中文版《电路基础》(原书第 6 版)配套作业用书"

# 补进检索行的词 —— 学生就是这么问的
_EXTRA_KW = "详细解析 讲解 详解 思路 步骤 方法 怎么做 怎么求 怎么算 中文版"

# 已有中文详细解析的标记(用来避免重复跑 import 时把增强结果冲掉)
MARK_CN = "【中文详细解析】"

# ─── 中文作业答案的题号 ────────────────────────────────────────────
# **结尾不能用 \b**:Python 的 \w 匹配中文,"2.74下图所示电路" 里 "4" 与 "下"
# 都是 \w,`\b` 会失败 —— 实测因此漏掉 2.74。
# 也接受 "6-11" 这种连字符写法:第 6 章整章都用 `6-11` 而不是 `6.11`。
_PROB_LINE = re.compile(r"^\s*(\d{1,2})\s*[.．\-]\s*(\d{1,3})(?![0-9])", re.M)
# 题号后面这十几个字里必须有中文,否则是公式/纯数值行(例如行首的 "10.5 V")
_CN_CHAR = re.compile(r"[一-鿿]")
_CN_LOOKAHEAD = 15

# 文件名里声明它覆盖哪几章:"Z第1-2章 ..." → {1,2};"Z第9-第9章 ..." → {9}
_CH_IN_NAME = re.compile(r"第\s*(\d{1,2})(?:\s*[-–—~]\s*(\d{1,2}))?\s*章")
_Z_FILE = re.compile(r"^Z.*\.pdf$", re.I)
# 讲义文件名:"第10章-正弦稳态分析.pdf" / "第1章 基本概念R-ZYL.pdf"
_SLIDE_FILE = re.compile(r"^第\s*(\d{1,2})\s*章[\s\-_—]*(.*?)\.pdf$", re.I)


def _clean_title(s: str) -> str:
    """从文件名里抠出干净的章节名。

    '基本概念R-ZYL' → '基本概念'(ZYL 是张曰理老师的缩写);
    '分析方法R'     → '分析方法'(R 是课件版本标记,也要去掉)。
    """
    s = re.sub(r"[-_\s]*(?:R-ZYL|ZYL|R)$", "", s.strip(), flags=re.I)
    s = re.sub(r"[（(]中文版[)）]", "", s)
    return s.strip(" -_—") or "讲义"


# 章节目录名的中文数字(1..20)。只用于**兜底** —— 正常情况下章节目录已经存在,
# 由 chapter_dir_map() 直接读回来。之所以兜底也要拼成 "1第一章" 这个形状:
# 万一某个章节目录缺失,不能凭空造出 "1第1章" 这种平行目录,把语料劈成两半。
_CN_NUM = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
           "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八",
           "十九", "二十"]


def _fallback_dir(ch: int) -> str:
    name = _CN_NUM[ch] if 0 <= ch < len(_CN_NUM) else str(ch)
    return f"{ch}第{name}章"


def _expected_chapters(name: str) -> set[int]:
    """从文件名推这份文件该有哪些章。**这是切题号时的噪声过滤器**:
    第 10 章的文件里会冒出行首的 "3.92",第 11 章里会冒 "13.7"(都是别题的引用),
    用"这份文件只该有第 10 章"一过滤就干净了。
    """
    out: set[int] = set()
    for m in _CH_IN_NAME.finditer(name):
        out.add(int(m.group(1)))
        if m.group(2):
            out.add(int(m.group(2)))
    return out


def _pdf_text(path: Path, with_pages: bool = False) -> str:
    """抽 PDF 文本。with_pages=True 时保留 `[第N页]` 标记(讲义要用,便于回溯 PPT 页码)。"""
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            t = pg.extract_text() or ""
            if t.strip():
                parts.append(f"[第{i}页]\n{t}" if with_pages else t)
    return base._clean("\n".join(parts))


# ─── 中文作业答案:切题 ────────────────────────────────────────────

def split_cn_problems(text: str, chapters: set[int]) -> list[tuple[str, str]]:
    """把一份中文作业答案 PDF 的全文切成 [(题号, 正文), ...]。

    `chapters` 是这份文件该有的章号(见 _expected_chapters),用来滤掉别题引用。

    同一题号出现多次时**取正文最长的那一段**,不是取第一次 ——
    第 14 章开头有一行"作业题 14.0 画出本章思维导图 14.4（传递函数）…"的清单,
    pdfplumber 换行后这些清单项也会落在行首,于是 14.0 会先匹配到一行清单。
    取最长的就自然选中后面真正的正文。
    """
    hits: list[re.Match] = []
    for m in _PROB_LINE.finditer(text):
        if int(m.group(1)) not in chapters:
            continue
        if not _CN_CHAR.search(text[m.end(): m.end() + _CN_LOOKAHEAD]):
            continue  # 行首的纯数值/公式,不是题号
        hits.append(m)

    sections: dict[str, str] = {}
    head = text[: hits[0].start()].strip() if hits else text.strip()
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[m.start(): end].strip()
        prob = f"{int(m.group(1))}.{m.group(2)}"
        # 数值化的题号归一:6-11 与 6.11 是同一题
        if len(body) > len(sections.get(prob, "")):
            sections[prob] = body
    out = sorted(sections.items(), key=lambda kv: (int(kv[0].split(".")[0]), int(kv[0].split(".")[1])))
    return ([("前言", head)] if len(head) >= 40 and not hits[:1] else []) + out


# ─── 语料文本拼装 ──────────────────────────────────────────────────

def _where(ch: str) -> str:
    return f"第{ch}章" if ch else ""


def cn_header(ch: str, prob: str, src_name: str) -> str:
    """中文版作业答案的标题头(检索锚点)。"""
    kw: list[str] = []
    for x in ["电路基础", "作业", "答案", "习题", "解答", "参考", "中文版",
              _where(ch), f"CH{ch}", prob, f"题{prob}", _EXTRA_KW]:
        if x and x not in kw:
            kw.append(x)
    return (
        f"【{COURSE} {_where(ch)} 习题 {prob} 参考答案(中文版)】任课老师:{TEACHER}\n"
        f"来源:{src_name}({TEXTBOOK_CN},**参数与结论以本版为准**)\n"
        f"检索:{' '.join(kw)}\n"
    )


def en_reference_block(en_body: str, en_src: str) -> str:
    """英文版原文的降级区块。

    为什么要留:助教要求"之前的知识库仍然保留";而且两版对照能看出
    国际版哪里改了参数。
    为什么要写明"以中文版为准":这正是助教反馈的坑 ——
    **国际版个别题目参数不同(公里↔英里)**,不提醒的话大模型可能挑错那一版。
    """
    return (
        "【英文版对照(仅参考)】\n"
        f"来源:{en_src}(Alexander & Sadiku 第6版 Soln)\n"
        "⚠️ 这是**国际版**教材的解答,**个别题目的参数/单位与中文版不同**(例如公里与英里)。\n"
        "**数值、单位、结论一律以中文版为准**;本段只用于对照两版差异。\n"
        "\n" + en_body
    )


def slide_header(ch: str, title: str, src_name: str, pages: int) -> str:
    kw: list[str] = []
    for x in ["电路基础", "讲义", "课件", "课堂PPT", "概念", "定义", "原理", "定理",
              "定律", "方法", "讲解", "作业", "习题",
              _where(ch), f"CH{ch}", title]:
        if x and x not in kw:
            kw.append(x)
    return (
        f"【{COURSE} {_where(ch)} 讲义(张曰理老师课件)】{title}\n"
        f"来源:{src_name}(课堂 PPT 导出,共 {pages} 页)\n"
        f"检索:{' '.join(kw)}\n"
    )


# 错误集锦的检索行要**把学生的说法都列上**。实测:"教材上哪里有印错的地方"
# 里的"印错"在原检索行(印刷/错误)里一个词都对不上,结果这份资料排到第 9 名,
# 大模型就答"我这边没有拿到那份清单的内容";换成"教材哪里印错了"才排到第 2 名。
# 同一个意思、两种问法,一个答得了一个答不了 —— 所以这里把口语句式一并写进锚点。
ERRORS_HEADER = (
    f"【{COURSE} 错误集锦(教材勘误)】张曰理老师课件\n"
    f"来源:电路理论基础---错误集锦(电路教研组,2022-02-26)\n"
    f"检索:{COURSE} 错误 集锦 勘误 易错 常见错误 错题 订正 不用做 教材 印刷 "
    f"印错 印错了 写错 错字 哪里有错 哪里印错 教材错误 书上错了\n"
)


# ─── 英文版语料(读回英文原文,用于"对照") ──────────────────────────

def collect_en_problems(src: Path) -> dict[str, tuple[str, str]]:
    """{题号: (英文原文, 来源文件名)} —— 从英文 Soln PDF 现抽,不依赖已有语料的状态。"""
    out: dict[str, tuple[str, str]] = {}
    if not src.exists():
        _warn(f"[theory] ⚠️ 英文版源目录不存在,跳过「英文版对照」:{src}")
        return out
    for sub in sorted(p for p in src.iterdir() if p.is_dir()):
        m = base._CHAPTER_DIR.match(sub.name)
        if not m:
            continue
        ch_from_dir = str(int(m.group(1)))
        for pdf in sorted(sub.glob("*.pdf")):
            body = base._pdf_text(pdf)
            if len(body) < 60:
                continue
            body = base.strip_copyright(body)
            ch, prob, _how = base._problem_ref(pdf, body)
            if not ch:
                ch = ch_from_dir
            out[prob] = (body, pdf.name)
    return out


def chapter_dir_map(out: Path) -> dict[int, str]:
    """{章号: 章节目录名},如 {1:'1第一章', 10:'10第十章'}。

    从**已有语料目录**里读 —— 这样中文版就会写进和英文版同一个目录、
    同一个文件名(同名即同题,也就是"覆盖")。
    """
    result: dict[int, str] = {}
    if not out.is_dir():
        return result
    for d in out.iterdir():
        if not d.is_dir():
            continue
        m = base._CHAPTER_DIR.match(d.name)
        if m:
            result[int(m.group(1))] = d.name
    return result


# ─── 写盘 ─────────────────────────────────────────────────────────

def render(spec: Spec, chinese: str = "") -> str:
    """把一份规格拼成文件内容:标题头 →(可选)详细解析 →原文 →文末附加块。

    import 与 enhance 共用,区别只是传不传 chinese。这样做有个硬性好处:
    **增强脚本重写文件时不可能漏掉英文对照块** —— 它压根不自己拼。
    """
    parts = [spec.header]
    if chinese.strip():
        parts.append("\n" + MARK_CN + "\n" + chinese.strip() + "\n")
    parts.append("\n【原文(核对用,未经改动)】\n" + spec.body + "\n")
    if spec.extra:
        parts.append("\n" + spec.extra + "\n")
    return "".join(parts)


def guarded_write(dest: Path, text: str, force: bool) -> str:
    """写语料文件。返回 "written" / "rewritten"(覆盖了带详细解析的旧文件) / "kept"。

    默认**不覆盖**已经带【中文详细解析】的文件 —— 本脚本产出的是**没有解析的原始语料**,
    拿它盖掉解析是纯粹的倒退。

    ⚠️ 但这里有个容易搞混的点:那 53 道中英都有的题,**详细解析必须按中文版重写**
    (旧的解析是照英文版原文写的,而国际版个别题参数不同 —— 助教反馈的正是这个坑)。
    这件事由 **`enhance_circuit_basic.py` 负责**:它直接从源 PDF 重新抽中文原文生成,
    不读本脚本写的文件。所以正确顺序是:

        import_circuit_theory.py   → 落盘(讲义/错误集锦 + 题目原始语料)
        enhance_circuit_basic.py   → 把题目文件升级成"中文版详细解析 + 英文版对照"
        build_index.py             → 重建索引

    本脚本单独重跑很安全(默认全跳过);`--force` 才强制用原始语料覆盖。
    """
    had_enhanced = False
    if dest.exists():
        try:
            had_enhanced = MARK_CN in dest.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            pass
    if had_enhanced and not force:
        return "kept"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return "rewritten" if had_enhanced else "written"


@dataclass
class Spec:
    """一份语料的完整规格:写去哪、标题头、正文、文末附加块。

    **import 和 enhance 两个脚本都从这里取规格**,这样"文件路径"和"文件格式"
    只有一个来源。否则两边各拼一遍,迟早拼岔 —— 比如增强脚本重写文件时
    漏掉英文对照块,英文原文就悄悄没了。
    """
    dest: Path
    header: str
    body: str
    extra: str = ""


def theory_specs(src: Path, out: Path) -> list[Spec]:
    """中文版作业答案 → 每题的语料规格(不含大模型解析)。"""
    specs: list[Spec] = []
    en = collect_en_problems(src.parent / "电路基础课后作业答案")
    dmap = chapter_dir_map(out)

    for pdf in sorted(p for p in src.glob("*.pdf") if _Z_FILE.match(p.name)):
        chapters = _expected_chapters(pdf.name)
        if not chapters:
            _warn(f"[theory] ⚠️ {pdf.name}:文件名里读不出章号,跳过")
            continue
        text = _pdf_text(pdf)
        if len(text) < 60:
            _warn(f"[theory] ⚠️ {pdf.name}:可提取文本只有 {len(text)} 字符,跳过")
            continue
        problems = [(p, b) for p, b in split_cn_problems(text, chapters) if p != "前言"]
        for prob, body in problems:
            ch = prob.split(".")[0]
            chdir = dmap.get(int(ch)) or _fallback_dir(int(ch))
            en_hit = en.get(prob)
            specs.append(Spec(
                dest=out / chdir / f"{prob}.md",
                header=cn_header(ch, prob, pdf.name),
                body=body,
                extra=en_reference_block(*en_hit) if en_hit else "",
            ))
        _warn(f"[theory] 中文作业答案 {pdf.name}:切出 {len(problems)} 题 "
              f"(章 {sorted(chapters)})")
    return specs


def build(src: Path, out: Path, force: bool, dry_run: bool) -> dict:
    """产出全部语料。返回统计字典。"""
    stats = {"cn": 0, "kept": 0, "rewritten": 0, "cn_only": 0, "merged": 0,
             "slides": 0, "errors": 0, "skipped": []}

    # ── ① 中文版作业答案 ─────────────────────────────────────────
    for spec in theory_specs(src, out):
        stats["merged" if spec.extra else "cn_only"] += 1
        if dry_run:
            stats["cn"] += 1
            continue
        act = guarded_write(spec.dest, render(spec), force)
        stats["kept" if act == "kept" else "rewritten" if act == "rewritten" else "cn"] += 1

    # ── ② 课堂讲义 ───────────────────────────────────────────────
    for pdf in sorted(src.glob("*.pdf")):
        m = _SLIDE_FILE.match(pdf.name)
        if not m:
            continue
        ch = str(int(m.group(1)))
        title = _clean_title(m.group(2))
        text = _pdf_text(pdf, with_pages=True)
        if len(text) < 200:
            stats["skipped"].append((pdf.name, f"可提取文本过少({len(text)} 字符)"))
            continue
        pages = text.count("[第")
        body = slide_header(ch, title, pdf.name, pages) + "\n" + text + "\n"
        if not dry_run:
            guarded_write(out / "讲义" / f"第{ch}章-{title}.md", body, force)
        stats["slides"] += 1

    # ── ③ 错误集锦 ──────────────────────────────────────────────
    for pptx in sorted(src.glob("*.pptx")):
        if pptx.name.startswith("~$"):
            continue
        from src.utils.doc_parser import parse_document
        text = parse_document(pptx)
        if len(text) < 60:
            stats["skipped"].append((pptx.name, f"可提取文本过少({len(text)} 字符)"))
            continue
        if not dry_run:
            guarded_write(out / "错误集锦.md", ERRORS_HEADER + "\n" + text + "\n", force)
        stats["errors"] += 1

    # ── ④ 目录 + 清单 ───────────────────────────────────────────
    if not dry_run:
        _write_toc(out, problems_by_ch(chapter_dir_map(out), out))
        _write_manifest(out, src, stats)
    return stats


def problems_by_ch(dmap: dict[int, str], out: Path) -> dict[str, list[str]]:
    """{章节目录名: [题号...]} —— 从写好的语料目录现读,保证目录和实际内容一致。"""
    result: dict[str, list[str]] = {}
    for _ch, name in sorted(dmap.items()):
        d = out / name
        if d.is_dir():
            probs = sorted((p.stem for p in d.glob("*.md")),
                           key=lambda s: (int(s.split(".")[0]), int(s.split(".")[1]))
                           if s.replace(".", "").isdigit() else (999, 999))
            if probs:
                result[name] = probs
    return result


def _write_toc(out: Path, by_ch: dict[str, list[str]]) -> None:
    lines = [
        f"# {COURSE} 资料总目录",
        f"任课老师:{TEACHER}。知识库里与《电路基础》有关的全部资料清单。",
        "",
        "## 1. 作业答案",
        f"**以中文版为准**(中文版作业用书与国际版教材个别题目参数不同,如公里↔英里)。",
        "每个题号一个文件,文件名就是题号;文件里若同时有中英两版,英文版会降级成",
        "文末的「英文版对照(仅参考)」。",
        "",
    ]
    for chdir, probs in sorted(by_ch.items(), key=lambda kv: int(re.match(r"\d+", kv[0]).group())):
        lines.append(f"- {chdir}:{'、'.join(probs)}")
    lines += [
        "",
        "## 2. 课堂讲义(概念题的权威依据)",
        "`讲义/` 目录下按章存放,来源是张曰理老师的课堂 PPT。",
        "问「什么是叠加原理」这类概念题时,优先依据这里,而不是凭通用教材知识。",
        "",
        "## 3. 错误集锦",
        "`错误集锦.md` —— 教材勘误(哪一页印错了、正确写法是什么),",
        "其中还有「此题错误,不用做」这类提示。",
        "",
    ]
    (out / "00_资料总目录.md").write_text("\n".join(lines), encoding="utf-8")
    # 旧的英文版目录已被本目录取代,删掉免得索引里多一份同质文件
    stale = out / "00_作业答案目录.md"
    if stale.exists():
        stale.unlink()
        _warn("[theory] 已删除被取代的旧文件:00_作业答案目录.md")


def _write_manifest(out: Path, src: Path, stats: dict) -> None:
    (out / "_manifest_theory.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(src),
        "output_dir": str(out),
        **{k: v for k, v in stats.items() if k != "skipped"},
        "skipped": [{"file": f, "reason": r} for f, r in stats["skipped"]],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=None,
                    help="默认落到 MATERIALS_DIR/电路基础/")
    ap.add_argument("--force", action="store_true",
                    help="用原始语料覆盖已有【中文详细解析】的文件(默认跳过保护)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out: Path = args.out or (config.MATERIALS_DIR / COURSE)
    if not args.src.exists():
        _warn(f"[theory] 源目录不存在:{args.src}")
        return 1

    _warn(f"[theory] 源目录:{args.src}")
    _warn(f"[theory] 输出到:{out}")
    stats = build(args.src, out, args.force, args.dry_run)

    _warn(f"[theory] 中文版作业答案:{stats['cn'] + stats['rewritten']} 题"
          f"(其中与英文版合并 {stats['merged']}、中文版独有 {stats['cn_only']};"
          f"覆盖了旧的英文版详细解析 {stats['rewritten']} 题、跳过已有解析 {stats['kept']} 题)")
    _warn(f"[theory] 讲义 {stats['slides']} 篇,错误集锦 {stats['errors']} 篇")
    for f, r in stats["skipped"]:
        _warn(f"[theory]     跳过 {f} ← {r}")
    if not args.dry_run:
        _warn(f"[theory] 下一步:跑增强把详细解析按中文原文补回来,再重建索引")
        _warn(f"[theory]   python scripts/enhance_circuit_basic.py --workers 5 && "
              f"python scripts/build_index.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
