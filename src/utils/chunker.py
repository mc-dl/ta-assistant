"""文本分块工具:按中文段落 + 标点切分,控制块大小和重叠。"""
from __future__ import annotations

import re

CN_SENTENCE_END = re.compile(r"(?<=[。!?!?;;])")


def split_text(
    text: str,
    chunk_size: int = 400,
    overlap: int = 50,
) -> list[str]:
    """把文本切成大小约等于 chunk_size 字的块,相邻块重叠 overlap 字。

    策略:
    1. 先按段落(连续空行)切
    2. 段内若超长,按中文句末标点继续切
    3. 合并小段到目标长度
    """
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # 段内拆句
    sentences: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            sentences.append(para)
        else:
            parts = [s for s in CN_SENTENCE_END.split(para) if s.strip()]
            sentences.extend(parts)

    # 合并到目标大小
    chunks: list[str] = []
    buf: list[str] = []
    cur = 0
    for s in sentences:
        if cur + len(s) <= chunk_size or not buf:
            buf.append(s)
            cur += len(s)
        else:
            chunks.append("".join(buf).strip())
            # 重叠:保留最后 overlap 字作为下一块开头
            if overlap > 0:
                tail = "".join(buf)[-overlap:]
                buf = [tail, s]
                cur = len(tail) + len(s)
            else:
                buf = [s]
                cur = len(s)
    if buf:
        chunks.append("".join(buf).strip())
    return [c for c in chunks if c]
