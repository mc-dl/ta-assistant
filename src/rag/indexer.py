"""RAG 索引构建:扫描数电资料目录,分块,建 BM25 索引,持久化到 pickle。"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, asdict
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from src import config
from src.utils.chunker import split_text
from src.utils.doc_parser import parse_document
from src.utils.stderr_log import warn as _warn

SUPPORTED_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xlsm", ".txt", ".md"}


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
        for piece in split_text(text, chunk_size, overlap):
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
