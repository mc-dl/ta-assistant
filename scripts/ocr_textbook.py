#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把**扫描版**教材 OCR 成文本,按页缓存(可断点续跑)。

**为什么需要这个脚本**:
    `电路基础-中文版-原书第6版（扫描版）.pdf`(711 页 / 266 MB)是**纯扫描件** ——
    每页一张 1904×2944 的图(`Pdg2Pic` + `FreePic2Pdf` 生成),`get_text()` 全文只有 115 个
    字符(还是元数据里的书名)。`pdfplumber` 一个字都抽不出来,所以它一直没进知识库。

    OCR 之后它是**概念题最权威的依据** —— 比课堂 PPT(只有提纲)细,比作业答案广。

**引擎选了 RapidOCR(ONNX)**:
    - 服务器 `sudo` 要密码,装不了 `tesseract-ocr`(apt 走不通);
    - RapidOCR 是纯 pip 依赖,自带中英文模型,CPU 就能跑,中文识别质量明显好于 tesseract-chi_sim;
    - 实测 300 DPI 下 2.3~3.2 秒/页,24 核并行约 10 分钟能跑完 711 页。

**输出**:每页一个 txt,**按页缓存**。跑一半断了重跑只会补没做的页。
    缓存目录**不在** `materials/` 下 —— 它是中间产物,不该进索引。

用法:
    # 先试几页,确认质量与耗时
    python scripts/ocr_textbook.py --pages 12,40,200 --preview

    # 全量(8 个进程;断点续跑,已缓存的页自动跳过)
    python scripts/ocr_textbook.py --workers 8
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# 必须在 import onnxruntime **之前**设置:每个进程单线程,
# 靠多进程而不是靠 intra-op 线程吃满 CPU(实测多进程吞吐高得多)
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

DEFAULT_PDF = Path(
    "/mnt/c/Users/52880/Downloads/202509电路理论基础/"
    "电路基础-中文版-原书第6版（扫描版）.pdf"
)
DEFAULT_CACHE = Path("/home/jj/ykt_questions/_ocr_cache/电路基础-6th")
DPI = 300


def _sort_boxes(res: list) -> list[str]:
    """把识别结果按**阅读顺序**排好再取文字。

    RapidOCR 自己也会排,但对"图注和正文混排"的页面偶尔会把图注插进正文中间。
    这里按 y 中心把框聚成"行"(同一行的框 y 中心相差不超过半行高),行内按 x 排 ——
    单栏排版下比引擎自带的顺序稳。**不做多栏处理**(本书是单栏)。
    """
    items = []
    for box, text, _score in res:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        h = max(ys) - min(ys) or 1.0
        items.append({"y": (min(ys) + max(ys)) / 2, "x": min(xs), "h": h, "t": text})
    if not items:
        return []
    items.sort(key=lambda d: d["y"])
    lines: list[list[dict]] = [[items[0]]]
    ref_h = items[0]["h"]
    for it in items[1:]:
        last = lines[-1][-1]
        if abs(it["y"] - last["y"]) <= max(ref_h, it["h"]) * 0.6:
            lines[-1].append(it)
        else:
            lines.append([it])
            ref_h = it["h"]
    out = []
    for ln in lines:
        ln.sort(key=lambda d: d["x"])
        out.append(" ".join(d["t"] for d in ln))
    return out


def _ocr_page(engine, doc, pno: int) -> str:
    """OCR 第 pno 页(1 起),返回纯文本。"""
    import numpy as np
    import pymupdf

    pg = doc[pno - 1]
    pm = pg.get_pixmap(dpi=DPI)
    arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)
    if pm.n == 4:
        arr = arr[:, :, :3]
    res, _ = engine(arr)
    if not res:
        return ""
    return "\n".join(_sort_boxes(res))


# ─── 多进程 worker ────────────────────────────────────────────────

_W = {}


def _init_worker(pdf: str, cache: str):
    """每个进程自己开一个文档句柄和一个引擎(PyMuPDF/ORT 都不宜跨进程共享)。"""
    import pymupdf
    from rapidocr_onnxruntime import RapidOCR

    _W["doc"] = pymupdf.open(pdf)
    _W["engine"] = RapidOCR()
    _W["cache"] = cache


def _work(pno: int):
    """一个页的任务:已缓存就跳过,否则 OCR 落盘。返回 (页号, 状态, 字符数)。

    传的是**页号**(int),不是闭包 —— 子进程只能拿到可 pickle 的东西。
    缓存目录由 _init_worker 放进 `_W`,不从这里传。
    """
    f = Path(_W["cache"]) / f"p{pno:04d}.txt"
    if f.exists() and f.stat().st_size > 0:
        return (pno, "cached", f.stat().st_size)
    try:
        text = _ocr_page(_W["engine"], _W["doc"], pno)
    except Exception as exc:  # noqa: BLE001
        return (pno, f"ERR {exc}", 0)
    f.write_text(text, encoding="utf-8")
    return (pno, "ok", len(text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--pages", default=None, help="只做这几页,如 12,40,200")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--preview", action="store_true", help="把文本打到屏幕上")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"PDF 不存在:{args.pdf}")
        return 1
    args.cache.mkdir(parents=True, exist_ok=True)

    import pymupdf

    total = pymupdf.open(args.pdf).page_count
    pages = ([int(x) for x in args.pages.split(",")] if args.pages
             else list(range(1, total + 1)))

    t0 = time.time()
    print(f"[ocr] {args.pdf.name}:{total} 页,本次处理 {len(pages)} 页,{args.workers} 进程")
    print(f"[ocr] 缓存目录:{args.cache}")

    if args.workers <= 1 or len(pages) == 1:
        _init_worker(str(args.pdf), str(args.cache))
        results = [_work(p) for p in pages]
    else:
        import multiprocessing as mp

        ctx = mp.get_context("fork")   # fork:省掉每个进程重新 import 的开销
        with ctx.Pool(args.workers, initializer=_init_worker,
                      initargs=(str(args.pdf), str(args.cache))) as pool:
            results = []
            for r in pool.imap_unordered(_work, pages):
                results.append(r)
                done = len(results)
                if done % 25 == 0 or done == len(pages):
                    el = time.time() - t0
                    print(f"[ocr]   {done}/{len(pages)}  {el:.0f}s  预计剩余 "
                          f"{el / done * (len(pages) - done) / 60:.1f} 分钟", flush=True)

    n_ok = sum(1 for _p, s, _c in results if s in ("ok", "cached"))
    n_err = [(p, s) for p, s, _c in results if s.startswith("ERR")]
    n_empty = [(p, c) for p, s, c in results if s == "ok" and c == 0]
    chars = sum(c for _p, s, c in results if s in ("ok", "cached"))
    print(f"[ocr] 完成:{n_ok}/{len(pages)} 页,共 {chars} 字符,"
          f"耗时 {(time.time() - t0) / 60:.1f} 分钟")
    if n_empty:
        print(f"[ocr] ⚠️ 识别为空(可能是纯图片页):{[p for p, _ in n_empty][:20]}")
    if n_err:
        print(f"[ocr] ❌ 失败 {len(n_err)} 页:{n_err[:10]}")

    if args.preview:
        for p in pages[:3]:
            f = args.cache / f"p{p:04d}.txt"
            if not f.exists():
                continue
            print("=" * 70)
            print(f"第 {p} 页")
            print("=" * 70)
            print(f.read_text(encoding="utf-8")[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
