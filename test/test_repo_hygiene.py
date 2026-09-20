"""仓库卫生:不许出现 CRLF 行尾。

为什么值得单独一个测试文件:本项目的用法是「Windows 侧编辑 → rsync 到 WSL 侧运行」,
rsync 搬的是**原样字节**,所以 Windows 上被写成 CRLF 的文件到了 WSL 还是 CRLF。后果不是
"看起来丑",而是**真的坏**:

  - `scripts/setup.sh` 带 CRLF 时 `bash -n` 报 `syntax error: unexpected end of file`
    (shebang 变成 `#!/usr/bin/env bash\\r`,别的行也会撞到 `\\r`)。症状指向文件末尾,
    很容易误导人去查括号/`fi` 是否配对。
  - `docs/*.html` 由 `scripts/md2html.py` 生成(显式 `newline="\\n"`)。检出时若被转成
    CRLF,"网页层是否和 md 同步"就没法用
    `python scripts/md2html.py --all docs/ --hub && git diff --stat docs/` 判断。

`.gitattributes` 的 `* text=auto eol=lf` 管住 git 检出这一路;这个测试管住另一路 ——
有人用记事本之类工具改完文件,把 CRLF 又写回工作树。两道一起才拦得住。

Python 源码其实不怕 CRLF(标准库按 universal newlines 读),但这里一并扫,理由是
"整个工作树的文本文件统一 LF"比"记得哪些后缀怕 CRLF"可靠得多。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 按后缀判断文本文件。图片/xlsx/pkl 等二进制不在内 ——
# 二进制里任何一个 0x0D 字节都会被误判成 CRLF,照着扫会得出假阳性。
TEXT_SUFFIXES = {
    ".py", ".sh", ".md", ".html", ".txt", ".json", ".yml", ".yaml",
    ".toml", ".cfg", ".ini", ".css", ".js", ".ts",
}
TEXT_NAMES = {".gitattributes", ".gitignore", "env.example", "LICENSE"}

# .venv 里几万个文件,必须剪枝而不是先遍历再过滤
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}


def _text_files() -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix in TEXT_SUFFIXES or name in TEXT_NAMES:
                out.append(p)
    return sorted(out)


def _first_crlf_line(raw: bytes) -> int | None:
    """返回第一个 CRLF 出现的行号(1 起);没有则 None。"""
    i = raw.find(b"\r\n")
    return None if i == -1 else raw[:i].count(b"\n") + 1


def test_scanner_actually_finds_files():
    """先确认这个测试本身没瞎:扫不到文件的话下面那条会空过。"""
    files = _text_files()
    names = {p.name for p in files}
    assert len(files) > 20, f"只扫到 {len(files)} 个文本文件,扫描逻辑大概坏了"
    assert "setup.sh" in names, "连 scripts/setup.sh 都没扫到"


def test_no_crlf_line_endings():
    """整个工作树的文本文件都必须是 LF。

    一次报出**全部**违规文件而不是遇到第一个就 fail —— 这种问题通常是一次性带回来一批
    (17 个文件里 15 个是同一批),一次看完才好一起修。
    """
    offenders = []
    for path in _text_files():
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:  # 后缀像文本、其实是二进制,放过
            continue
        line = _first_crlf_line(raw)
        if line is not None:
            offenders.append(f"  {path.relative_to(REPO)} (第 {line} 行起)")
    assert not offenders, (
        "以下文件是 CRLF 行尾,会被 rsync 原样带到 WSL:\n"
        + "\n".join(offenders)
        + "\n\nshell 脚本会直接跑不起来,生成的 HTML 会让 git diff 满屏是红。"
        "\n转成 LF 再提交(见 docs/deployment.md「行尾必须是 LF」)。"
    )


def test_gitattributes_pins_lf():
    """`.gitattributes` 必须显式钉住 eol=lf。

    本机 core.autocrlf=true 是 Windows 常见默认,它会把检出的文本文件转成 CRLF;
    不写 eol=lf 就盖不住,fresh clone 出来的 HTML 会是 CRLF。
    """
    ga = REPO / ".gitattributes"
    assert ga.is_file(), "缺 .gitattributes —— fresh clone 的 HTML 会是 CRLF"
    body = ga.read_text(encoding="utf-8")
    assert "eol=lf" in body, "没有 eol=lf,autocrlf=true 的机器上盖不住"
    assert "*.png binary" in body, "图片要声明成 binary,否则可能被当文本处理"
