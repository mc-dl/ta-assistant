"""`scripts/md2html.py` 的回归测试。

这个脚本是**给文档做展示层**的,坏了不会让系统答错话,但会静默产出一份
"看起来正常、其实图没渲染/占位符还在"的 HTML —— 所以按"产物里有什么"来钉:

- mermaid 围栏**必须变成** `<div class="mermaid">`,而且不能留下 `MERMAIDBLOCK` 占位符
  (留在正文里就是明晃晃的内部标记,最难看的一种坏法);
- 表格、图片、引用块都要被包成对应的构件;
- 二级标题要有 id + 锚点(不然侧边目录点了没反应);
- `--hub` 要真的生成 `index.html`,而不是只打印一行 ok。

不测配色、不测排版细节 —— 那些改 CSS 就会动,钉不住也不值得钉。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    """按路径加载:scripts/ 不是包,不能 import scripts.md2html。"""
    spec = importlib.util.spec_from_file_location("md2html", ROOT / "scripts" / "md2html.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["md2html"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def md2html():
    return _load_module()


SAMPLE = """# 示例文档

导语一段,用来当 meta description。

> ⚠️ 这是个警告块

## 第一节

| 甲 | 乙 |
|---|---|
| 1 | 2 |

```mermaid
flowchart LR
  A["甲"] --> B["乙"]
```

![一张图](images/x.png)

## 第二节

> ✅ 这是个通过块

正文里有 `代码` 和 [外链](https://example.com)。
"""


@pytest.fixture
def sample_md(tmp_path: Path) -> Path:
    p = tmp_path / "sample.md"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def _render(md2html, md: Path, nav=None) -> str:
    out = md.with_suffix(".html")
    md2html.convert(md, out, nav=nav)
    return out.read_text(encoding="utf-8")


def test_mermaid_fence_becomes_div_and_leaves_no_placeholder(md2html, sample_md):
    html = _render(md2html, sample_md)
    assert '<div class="mermaid">' in html
    # 围栏语言标记本身不该出现在正文里
    assert "```mermaid" not in html
    assert "MERMAIDBLOCK" not in html, "占位符泄漏了 —— 正文里会看到内部标记"
    # 离线兜底:源码另存一份在折叠区
    assert "Mermaid 源码" in html
    # 只有图才需要加载 mermaid 运行库
    assert "mermaid@11" in html


def test_no_mermaid_means_no_cdn_script(md2html, tmp_path: Path):
    md = tmp_path / "plain.md"
    md.write_text("# 无图文档\n\n## 一节\n\n正文。\n", encoding="utf-8")
    html = _render(md2html, md)
    assert "mermaid@11" not in html, "没图还去拉 1MB 的 CDN 脚本,纯属浪费"


def test_table_wrapped_for_horizontal_scroll(md2html, sample_md):
    html = _render(md2html, sample_md)
    assert html.count('<div class="table-wrap"><table>') == 1
    assert "</table></div>" in html


def test_image_becomes_figure_with_caption_from_alt(md2html, sample_md):
    html = _render(md2html, sample_md)
    assert '<figure class="pic">' in html
    assert "<figcaption>一张图</figcaption>" in html


def test_headings_get_anchor_and_id(md2html, sample_md):
    html = _render(md2html, sample_md)
    assert 'class="anchor"' in html
    # 中文标题的 id 要保留中文(slugify_unicode),否则锚点是 "0"/"1" 这种
    assert 'id="第一节"' in html


@pytest.mark.parametrize(
    "marker,cls",
    [("⚠️", "warn"), ("✅", "ok"), ("❌", "bad")],
    ids=["警告", "通过", "禁止"],
)
def test_blockquote_callout_colored_by_emoji(md2html, tmp_path: Path, marker, cls):
    md = tmp_path / "c.md"
    md.write_text(f"# 标题\n\n## 一节\n\n> {marker} 一句话\n", encoding="utf-8")
    html = _render(md2html, md)
    assert f"<blockquote class=\"{cls}\">" in html


def test_plain_blockquote_keeps_default_style(md2html, tmp_path: Path):
    md = tmp_path / "q.md"
    md.write_text("# 标题\n\n## 一节\n\n> 普通引用,不带 emoji\n", encoding="utf-8")
    html = _render(md2html, md)
    assert "<blockquote>" in html


def test_quick_strip_and_toc_present_when_enough_sections(md2html, tmp_path: Path):
    # 胶囊条只在**小节够多**时才出现(3 个以上):两节的短文档顶上加一排胶囊纯属噪音
    md = tmp_path / "many.md"
    md.write_text(
        "# 多节文档\n\n" + "\n\n".join(f"## 第 {i} 节\n\n正文。" for i in range(1, 5)),
        encoding="utf-8",
    )
    html = _render(md2html, md, nav=[("many", "多节文档"), ("other", "另一篇")])
    assert 'class="quick"' in html
    assert 'class="card toc"' in html
    assert 'class="card docs-nav"' in html
    assert 'class="here"' in html, "当前页在文档导航里要高亮"


def test_quick_strip_skipped_for_short_docs(md2html, tmp_path: Path):
    md = tmp_path / "short.md"
    md.write_text("# 短文档\n\n## 只有一节\n\n正文。\n", encoding="utf-8")
    html = _render(md2html, md)
    assert 'class="quick"' not in html


def test_title_and_description_taken_from_content(md2html, sample_md):
    html = _render(md2html, sample_md)
    assert "<title>示例文档</title>" in html
    assert 'content="导语一段' in html


def test_cli_all_and_hub(md2html, tmp_path: Path, monkeypatch, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, title in [("a", "甲文档"), ("b", "乙文档")]:
        (docs / f"{name}.md").write_text(f"# {title}\n\n## 一节\n\n正文。\n", encoding="utf-8")

    monkeypatch.setattr(
        sys, "argv", ["md2html.py", "--all", str(docs), "--hub"]
    )
    assert md2html.main() == 0

    for name in ("a", "b"):
        assert (docs / f"{name}.html").is_file()
    hub = docs / "index.html"
    assert hub.is_file(), "--hub 必须真的生成导航页"
    text = hub.read_text(encoding="utf-8")
    assert "a.html" in text and "b.html" in text
    # 中转用的临时 md 不能留在目录里(留着下一轮 --all 会把它也转一遍)
    assert not (docs / "_hub_input.md").exists()
    out = capsys.readouterr().out
    assert "hub ->" in out
