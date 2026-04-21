"""检索器:BM25(数电资料)和 FAQRetriever(常问问题.txt)。"""
from __future__ import annotations

import pickle
import re
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from src import config
from src.utils.stderr_log import warn as _warn


class BM25Retriever:
    """基于 pickle 索引的 BM25 检索。"""

    def __init__(self, bm25: BM25Okapi, chunks: list[dict]):
        self.bm25 = bm25
        self.chunks = chunks

    @classmethod
    def load(cls, path: Path | None = None) -> "BM25Retriever":
        path = path or config.BM25_INDEX_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"索引文件不存在:{path}。请先运行 `python scripts/build_index.py`"
            )
        with path.open("rb") as f:
            data = pickle.load(f)
        return cls(bm25=data["bm25"], chunks=data["chunks"])

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not query.strip():
            return []
        tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokens)
        idxs = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
        return [
            {
                **self.chunks[i],
                "score": float(scores[i]),
            }
            for i in idxs
            if scores[i] > 0
        ]


class FAQRetriever:
    """FAQ 检索:吃常问问题.txt,每次启动现建 BM25(数据量小,很快)。"""

    def __init__(self, qa_pairs: list[tuple[str, str]]):
        self.qa_pairs = qa_pairs
        if qa_pairs:
            tokenized = [list(jieba.cut(q)) for q, _ in qa_pairs]
            self.bm25 = BM25Okapi(tokenized)
        else:
            self.bm25 = None

    @classmethod
    def from_default(cls) -> "FAQRetriever":
        return cls.from_file(config.FAQ_PATH)

    @classmethod
    def from_file(cls, path: Path) -> "FAQRetriever":
        if not path.exists():
            _warn(f"[FAQRetriever] FAQ 文件不存在:{path}")
            return cls([])
        text = path.read_text(encoding="utf-8", errors="ignore")
        return cls(_parse_faq(text))

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        if not query.strip() or not self.bm25 or not self.qa_pairs:
            return []
        tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokens)
        idxs = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
        return [
            {
                "question": self.qa_pairs[i][0],
                "answer": self.qa_pairs[i][1],
                "score": float(scores[i]),
            }
            for i in idxs
            if scores[i] > 0
        ]


_Q_PREFIX = re.compile(r"^\s*Q\s*[:：]\s*", re.IGNORECASE)
_A_PREFIX = re.compile(r"^\s*A\s*[:：]\s*", re.IGNORECASE)


def _parse_faq(text: str) -> list[tuple[str, str]]:
    """解析 `Q:\n...\nA:\n...\n\n` 格式。Q/A 可以多行,空行分隔条目。"""
    pairs: list[tuple[str, str]] = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _Q_PREFIX.match(line) or line.upper().startswith("Q:") or line.startswith("Q:"):
            # 收集 Q
            q_lines = [_Q_PREFIX.sub("", line)]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if _A_PREFIX.match(nxt) or nxt.upper().startswith("A:") or nxt.startswith("A:"):
                    break
                if nxt == "":
                    break
                q_lines.append(nxt)
                i += 1

            # 收集 A
            a_lines: list[str] = []
            if i < len(lines):
                cur = lines[i].strip()
                if _A_PREFIX.match(cur) or cur.upper().startswith("A:") or cur.startswith("A:"):
                    a_lines.append(_A_PREFIX.sub("", cur))
                    i += 1
                    while i < len(lines):
                        nxt = lines[i].strip()
                        # 空行 + 下一条 Q 结束当前条目
                        if nxt == "" and (
                            i + 1 < len(lines)
                            and (lines[i + 1].strip().startswith("Q:")
                                 or lines[i + 1].strip().startswith("Q:"))
                        ):
                            break
                        if nxt.startswith("Q:") or nxt.startswith("Q:"):
                            break
                        a_lines.append(lines[i])
                        i += 1

            q = "\n".join(q_lines).strip()
            a = "\n".join(a_lines).strip()
            if q and a:
                pairs.append((q, a))
        else:
            i += 1
    return pairs
