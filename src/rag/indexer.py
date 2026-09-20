"""RAG 索引构建:扫描数电资料目录,分块,建 BM25 索引,持久化到 pickle。"""
from __future__ import annotations

import pickle
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from src import config
from src.rag.retriever import apply_lucene_idf
from src.utils.chunker import split_text
from src.utils.doc_parser import parse_document
from src.utils.stderr_log import warn as _warn

SUPPORTED_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xlsm", ".txt", ".md"}

# 语料开头的「检索:」锚点行(由 scripts/import_circuit_basic.py 生成)。
# 只认文档**开头**的,避免误抓正文深处偶然出现的"检索:"。
#
# ── 为什么要把锚点复制进每一块(2026-09-20)────────────────────────
# 实测学生问「电路基础第一章作业的1.36怎么做？」,而 1.36.md 的五个分块得分是:
#     第 0 块(锚点行+题目要求) 20.77   ← 只有它带着锚点
#     第 1 块(已知条件)         2.27
#     第 2 块(详细步骤)         0.00   ← 解答就在这里
#     第 3 块(最终答案)         5.90
#     第 4 块(英文原文)         0.00
# 于是 top-5 被**别的文件的第 0 块**占满(它们都含「电路基础/作业/答案/第N章」,
# 各得 ~12.5 分),模型只拿到"题目要求"、拿不到解题步骤,如实回答
# "资料里只有题目,没有解答步骤" —— 学生看到的就是"答不出来"。
#
# 根因:锚点行只为检索而生,却只落在第一块上。复制进每一块即可,副作用还是好的 ——
# 锚点里的通用词(电路基础/作业/答案/习题)因此在**所有**块里都出现,IDF 自动趋近 0,
# 真正有区分度的题号(1.36)反而更突出。
_ANCHOR_LINE = re.compile(r"^检索[:：].*$", re.M)

# 教材语料的**印刷页码**标记(由 scripts/import_textbook.py 生成)。
# 尾括号故意不写在正则里:标记可能正好被 overlap 切开(`…[教材 P34` + `0]…`),
# 不带尾括号才认得出被切一半的那种。`\d+` 遇到 `]` 自然停下,不会多吞。
_PAGE_MARK = re.compile(r"\[教材 P(\d+)")


def _anchor_of(text: str) -> str:
    """取出文档开头的『检索:』锚点行;没有(如数电的原始 PDF)则返回空串。"""
    m = _ANCHOR_LINE.search(text[:400])
    return m.group(0).strip() if m else ""


@dataclass
class Chunk:
    chunk_id: int
    text: str
    source: str   # 源文件名(不含路径)
    path: str     # 源文件绝对路径


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            # 跳过 Office 临时文件
            if p.name.startswith("~$"):
                continue
            # 跳过人工目录/清单类文件(约定:00_ 开头是目录,_ 开头是元数据)。
            # 为什么要专门跳过它们:`00_资料总目录.md` 里列着**每个题号和章节名**,
            # 于是**任何**带题号或章节关键词的提问它都能拿高分 ——
            # 实测它是"什么是叠加原理""1.36 怎么做""教材哪里印错"这些问题的
            # 共同第一名(13~17 分),把真正该出现的讲义/答案挤到第 9~18 名。
            # 它是给人看的清单,不是给人问的资料,所以不进索引。
            if p.name.startswith(("00_", "_")):
                continue
            yield p


def build_index(
    materials_dir: Path | None = None,
    out_path: Path | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    verbose: bool = True,
) -> int:
    """返回索引的块数。"""
    materials_dir = materials_dir or config.MATERIALS_DIR
    out_path = out_path or config.BM25_INDEX_PATH
    chunk_size = chunk_size or config.CHUNK_SIZE
    overlap = overlap or config.CHUNK_OVERLAP

    if not materials_dir.exists():
        raise FileNotFoundError(f"资料目录不存在:{materials_dir}")

    chunks: list[Chunk] = []
    cid = 0
    file_count = 0
    for f in _iter_files(materials_dir):
        if verbose:
            _warn(f"[indexer] 解析: {f.name}")
        text = parse_document(f)
        if not text.strip():
            if verbose:
                _warn(f"  ⚠️ 无内容或解析失败,跳过")
            continue
        file_count += 1
        anchor = _anchor_of(text)
        cur_page: str | None = None          # 本文件**当前所在**的印刷页(最近一次见到的标记)
        for i, piece in enumerate(split_text(text, chunk_size, overlap)):
            # ── 页码标记也要复制进每一块(2026-09-20 审计发现)────────────
            # 和锚点同一个病:`[教材 P340]` 自成一段,`split_text` 按段落合并时它
            # 只落进该页的**第一块**,同页后面几块就丢了页码。审计实测:
            # 教材 2106 块里**只有 754 块(35.8%)含页码标记**,其余 1352 块拿不到页码 ——
            # 于是「引用教材时说清页码」这条提示词对**近三分之二的教材内容**是空话。
            # 修法照抄锚点:记住最近见到的页码,给没标记的块补一个。
            #
            # 跨页块(开头属于上一页、后半个标记落在块中间)这里不处理:块里既然已经
            # 有标记,模型顺着读就知道"标记管它下面那段",补一个反而可能自相矛盾。
            marks = _PAGE_MARK.findall(piece)
            if marks:
                cur_page = marks[0]

            prefix = ""
            # 第 0 块本来就带着锚点,从第 1 块起补上(原因见 _ANCHOR_LINE 上方注释)
            if i > 0 and anchor and not piece.startswith(anchor):
                prefix += f"{anchor}\n"
            # 没有页码标记 = 上一页的续块,补上那一页的页码。
            # `cur_page is None`(数电 PDF 等没有标记的文件)时什么都不做 ——
            # 这条改动对非教材文件是**完全无操作**的。
            if not marks and cur_page is not None:
                prefix += f"[教材 P{cur_page}]\n"
            if prefix:
                piece = prefix + piece

            chunks.append(Chunk(
                chunk_id=cid,
                text=piece,
                source=f.name,
                path=str(f),
            ))
            cid += 1

    if not chunks:
        raise RuntimeError("没有提取到任何可索引内容")

    # 分词
    tokenized = [list(jieba.cut(c.text)) for c in chunks]
    bm25 = BM25Okapi(tokenized)
    # 修 IDF:BM25Okapi 会把"半数以上分块都含"的词的 IDF 统一兜底成一个正数,
    # 于是 `的`/`是`/`原理` 这类词变成正证据(详见 apply_lucene_idf 的注释)。
    # 建索引时改一次,装载时也会再改一次 —— 老 pickle 不必重建也能吃到这个修正。
    apply_lucene_idf(bm25)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        pickle.dump({
            "bm25": bm25,
            "chunks": [asdict(c) for c in chunks],
            "tokenized": tokenized,
        }, f)

    if verbose:
        _warn(f"[indexer] ✅ 索引已生成")
        _warn(f"  文件数: {file_count}")
        _warn(f"  分块数: {len(chunks)}")
        _warn(f"  保存至: {out_path}")
    return len(chunks)


if __name__ == "__main__":
    build_index()
