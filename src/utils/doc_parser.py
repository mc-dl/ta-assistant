"""文档解析:把 pdf/docx/doc/xlsx/txt 统一解析成纯文本。

用法:
    text = parse_document(Path("xxx.pdf"))
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl
from src.utils.stderr_log import warn as _warn


def parse_document(path: Path) -> str:
    """根据扩展名分派。失败返回空字符串,不抛异常。"""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return _parse_pdf(path)
        if ext == ".docx":
            return _parse_docx(path)
        if ext == ".doc":
            return _parse_doc(path)
        if ext in (".xlsx", ".xlsm"):
            return _parse_xlsx(path)
        if ext in (".txt", ".md"):
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        _warn(f"[doc_parser] 解析失败 {path.name}: {e}")
    return ""


def _parse_pdf(path: Path) -> str:
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"[第{i}页]\n{text}")
    return "\n\n".join(parts)


def _parse_docx(path: Path) -> str:
    import docx  # python-docx
    d = docx.Document(str(path))
    paragraphs = [p.text for p in d.paragraphs if p.text.strip()]
    # 表格内容也拼进来
    for tbl in d.tables:
        for row in tbl.rows:
            paragraphs.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(paragraphs)


def _parse_doc(path: Path) -> str:
    """老式 .doc:先调 libreoffice 转成 .docx,再用 python-docx 读。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "libreoffice", "--headless",
            "--convert-to", "docx",
            "--outdir", tmpdir,
            str(path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            _warn(f"[doc_parser] libreoffice 转换失败 {path.name}: {e}. "
                  f"请确认已安装: sudo apt install libreoffice-core")
            return ""
        converted = Path(tmpdir) / (path.stem + ".docx")
        if not converted.exists():
            return ""
        return _parse_docx(converted)


def _parse_xlsx(path: Path) -> str:
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        lines: list[str] = [f"[Sheet: {sheet}]"]
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                lines.append(" | ".join(cells))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
