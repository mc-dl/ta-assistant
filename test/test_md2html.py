"""`scripts/md2html.py` 的回归测试。

这个脚本是**给文档做展示层**的,坏了不会让系统答错话,但会静默产出一份
"看起来正常、其实图没渲染/占位符还在"的 HTML —— 所以按"产物里有什么"来钉:

- mermaid 围栏**必须变成**一张构建期画好的 SVG 文件(不是运行时拉 CDN),
  而且不能留下 `ZZPLACE` 占位符(留在正文里就是明晃晃的内部标记,最难看的一种坏法);
- `:::` 视觉块(卡/对比/步骤/时间线/提示/图)要变成对应构件,写错了要**报错**而不是静默吞掉;
- 表格、图片、引用块都要被包成对应的构件;
- 二级标题要有 id + 锚点(不然侧边目录点了没反应);
- `--hub` 要真的生成 `index.html`,而不是只打印一行 ok。

不测配色、不测排版细节 —— 那些改 CSS 就会动,钉不住也不值得钉。
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
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


def test_mermaid_fence_becomes_svg_file_and_leaves_no_placeholder(md2html, sample_md):
    """mermaid 围栏 → 构建期画好的 SVG 文件 + 一个源码折叠区。

    这里以前钉的是 `<div class="mermaid">` + 运行时从 CDN 拉 mermaid.js 现画。
    改成构建期出图之后,**要钉的性质变了但没变松** —— 反而更硬:

    - 图必须**真的落成一个 .svg 文件**,而且是个像样的 SVG(网页离线也要能看);
    - 占位符不能泄漏到正文里;
    - 源码要留在折叠区(读者想改图时,复制走的就是它);
    - **一点都不许再碰 CDN** —— 换掉它一半就是为了这个。
    """
    html = _render(md2html, sample_md)
    svg = sample_md.parent / "diagrams" / "sample-0.svg"
    assert svg.is_file(), "图没有落成 svg 文件 —— 页面上会是一个裂图"
    text = svg.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg"), "落出来的不是 SVG"
    assert "viewBox=" in text, "没有 viewBox 的 SVG 没法自适应缩放"

    assert '<img src="diagrams/sample-0.svg"' in html
    assert "```mermaid" not in html, "围栏语言标记漏进正文了"
    assert "ZZPLACE" not in html, "占位符泄漏了 —— 正文里会看到内部标记"
    assert "flowchart LR" in html, "折叠区里应该留着原始 mermaid 源码"
    assert "cdn.jsdelivr.net" not in html, "构建期出图了,不该再去拉 CDN"


def test_diagram_caption_comes_from_the_caption_comment(md2html, tmp_path: Path):
    """`%% caption: …` 变成图题 —— 图没有标题就等于没解释。"""
    md = tmp_path / "cap.md"
    md.write_text(
        "# 有图题\n\n## 一节\n\n```mermaid\nflowchart LR\n  %% caption: 这是一句图题\n"
        '  A["甲"] --> B["乙"]\n```\n',
        encoding="utf-8",
    )
    html = _render(md2html, md)
    assert html.count("<figcaption>这是一句图题</figcaption>") == 1
    # 图题也得当 alt —— 图裂了或还没加载时,读者至少知道这张图在讲什么
    assert 'alt="这是一句图题"' in html
    # `%% caption: …` 那一行**要**留在源码折叠区里:它就是原始 mermaid 的一部分,
    # 读者复制走整段去别处渲染时,图题应该跟着走,而不是留在这边。
    fold = re.search(r'<details class="mermaid-src">.*?</details>', html, re.S)
    assert fold, "没有源码折叠区"
    assert "%% caption: 这是一句图题" in fold.group(0)


def test_wide_diagram_scrolls_and_says_so(md2html, tmp_path: Path):
    """超过版心的图按原尺寸横向滚动,而且**必须在页面上说一句**。

    窄图缩一缩没事(比例 ≥1,不会缩字);宽图缩下去就是把字缩没 ——
    一张 2500px 的图塞进 850px 的正文栏,13px 的字只剩 4px。
    但光滚动还不够:不说的话读者只看到左三分之一,以为图就长这样,
    右边被裁掉的往往是流程的终点。所以 `wide` 和那句提示是**一对**,一起钉。
    """
    def render(stem: str, nodes: str) -> str:
        md = tmp_path / f"{stem}.md"
        md.write_text(f"# 宽窄\n\n## 一节\n\n```mermaid\nflowchart LR\n{nodes}\n```\n",
                      encoding="utf-8")
        return _render(md2html, md)

    # 只查正文里的标签,CSS 里那个 `.wide-hint` 规则永远在,拿它当证据等于没查
    def hint_of(html: str) -> bool:
        return '<p class="wide-hint">' in html

    narrow = render("narrow", '  A["甲"] --> B["乙"]')
    assert 'class="diagram"' in narrow
    assert not hint_of(narrow), "窄图不需要滚动提示"

    # 一路串下去,横向必然超过 1080px
    long_chain = "\n".join(
        f'  N{i}["节点节点节点节点节点 {i}"] --> N{i + 1}["节点节点节点节点节点 {i + 1}"]'
        for i in range(14)
    )
    wide = render("wide", long_chain)
    assert 'class="diagram wide"' in wide
    assert hint_of(wide), "宽图必须带上`可以横向滚动`的提示"


def test_no_mermaid_means_no_diagram_dir(md2html, tmp_path: Path):
    md = tmp_path / "plain.md"
    md.write_text("# 无图文档\n\n## 一节\n\n正文。\n", encoding="utf-8")
    html = _render(md2html, md)
    assert "diagrams" not in html, "没图还去建/引 diagrams/ 目录"
    assert not (tmp_path / "diagrams").exists(), "没图画,不该留下空目录"


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
    # 而且**必须还挂在 h2 上** —— 只查 id 在不在是不够的:
    # 曾经正则写成 `<h([23]) …>`,group(1) 只拿到那个数字,于是产出 `<2 id=…>`,
    # id 照样在,上面那两条断言照样过,但每一节标题都掉了样式、滚动高亮全找不到。
    assert '<h2 id="第一节">' in html
    assert not re.search(r"<[123]\b", html), "标题标签名被吃掉了(如 <2 id=…>)"
    assert "</h2>" in html and "</2>" not in html


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


def test_footer_shows_source_fingerprint_not_wall_clock(md2html, sample_md):
    """页脚印的是源 md 的内容指纹,不是生成时刻。

    这不是美观问题:印 `datetime.now()` 会让每跑一次 `--all` 都改写全部 8 份 HTML
    (内容没变、只有时间戳变),`git diff` 里一片红,反而看不出哪份 HTML 真的过期了。
    """
    html = _render(md2html, sample_md)
    want = hashlib.sha256(sample_md.read_bytes()).hexdigest()[:8]
    assert f"内容指纹 <code>{want}</code>" in html
    assert "生成于" not in html


def test_regenerating_unchanged_source_is_byte_identical(md2html, sample_md):
    """幂等:md 没变,重复生成必须一个字节都不变。

    这条是上一条的性质版 —— 有了它,「我是不是忘了重新生成 HTML」才可以用
    `md2html --all docs/ --hub && git diff --stat docs/` 直接回答。
    """
    out = sample_md.with_suffix(".html")
    md2html.convert(sample_md, out)
    first = out.read_bytes()
    md2html.convert(sample_md, out)
    assert out.read_bytes() == first


def test_fingerprint_changes_only_when_source_changes(md2html, tmp_path: Path):
    """指纹跟着内容走:改一个字节就变,别的文件的 HTML 不受牵连。"""
    docs = tmp_path / "docs"
    docs.mkdir()
    a, b = docs / "a.md", docs / "b.md"
    a.write_text("# 甲\n\n## 一节\n\n原始正文。\n", encoding="utf-8")
    b.write_text("# 乙\n\n## 一节\n\n正文。\n", encoding="utf-8")
    md2html.convert(a, docs / "a.html")
    md2html.convert(b, docs / "b.html")
    before_a = (docs / "a.html").read_bytes()
    before_b = (docs / "b.html").read_bytes()

    a.write_text("# 甲\n\n## 一节\n\n改过的正文。\n", encoding="utf-8")
    md2html.convert(a, docs / "a.html")
    md2html.convert(b, docs / "b.html")
    assert (docs / "a.html").read_bytes() != before_a, "改了 md,对应 HTML 必须变"
    assert (docs / "b.html").read_bytes() == before_b, "没动的 md,HTML 不该被牵连"


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


def test_hub_sidebar_does_not_link_to_the_temp_file(md2html, tmp_path: Path, monkeypatch):
    """首页侧边栏**不能**出现指向 `_hub_input.html` 的链接。

    hub 是"先写临时 md、再转、再删"。`_nav_of()` 如果把它也扫进导航,首页侧边栏
    就多出一条指向那个临时文件名 —— 而文件转完就删了,**点了 404**。
    卡片和表格那两条断言查不到它(它们查的是 a.html/b.html 在不在),
    所以这里单独盯住"导航里的每一个 href 都指得着真实存在的文件"。
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("a", "b"):
        (docs / f"{name}.md").write_text(f"# {name}\n\n## 一节\n\n正文。\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["md2html.py", "--all", str(docs), "--hub"])
    assert md2html.main() == 0

    text = (docs / "index.html").read_text(encoding="utf-8")
    assert "_hub_input" not in text, "临时文件名漏进了首页(侧边栏会指向一个已删除的文件)"
    nav = re.search(r'<div class="card docs-nav">.*?</div>', text, re.S)
    assert nav, "首页没有文档导航块"
    for href in re.findall(r'href="([^"]+)"', nav.group(0)):
        assert (docs / href).is_file(), f"导航里 {href} 指不到文件"
    # 首页自己那一项要亮着,否则"当前在哪一篇"就没了
    assert 'href="index.html" class="here"' in nav.group(0)


def test_hub_hero_kicker_does_not_leak_the_temp_filename(md2html, tmp_path: Path, monkeypatch):
    """首页 hero 左上角那行小字也不能是 `_hub_input`。

    和上面那条是同一个坑的第三处,而且是最难发现的一处:`here`(侧边栏)和
    `src_label`(页脚)都显式挡掉了临时文件名,**hero 的 kicker 是直接从
    `md_path.stem` 取的** —— 于是首页顶上明晃晃挂着一行 `_hub_input`,
    一个转完就删的内部临时文件名,出现在了对外发布的页面上。
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# 甲\n\n## 一节\n\n正文。\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["md2html.py", "--all", str(docs), "--hub"])
    assert md2html.main() == 0
    text = (docs / "index.html").read_text(encoding="utf-8")
    kicker = re.search(r'<div class="kicker">(.*?)</div>', text)
    assert kicker, "首页没有 hero kicker"
    assert kicker.group(1) == "INDEX", f"首页 kicker 是 {kicker.group(1)!r}"
    assert "_hub_input" not in text


# ---------------------------------------------------------------------------
# `:::` 视觉块
# ---------------------------------------------------------------------------

#: 块类型 → (块体, 产物里必须出现的 class)。
#: 加新块时往这里补一行,下面三条测试(能渲染/能报错/不吞)一起覆盖到。
BLOCK_SAMPLES = {
    "stats": ("- **169** | 在册学生 | 8 个班", 'class="stats"'),
    "compare": ("### 甲\n- 一条\n\n---\n\n### 乙\n- 另一条", 'class="cmp c2"'),
    "steps": ("- 第一步 | 说明", 'class="steps"'),
    "timeline": ("- 2026-01 | 标题 | 说明", 'class="timeline"'),
    "cards": ("- 卡片 | 说明", 'class="mini"'),
    "note": ("正文。", 'class="note"'),
    "fold": ("正文。", 'class="fold"'),
    "chart": ("- 甲 | 3\n- 乙 | 7", 'class="chart"'),
}


def _render_block_doc(md2html, tmp_path: Path, kind: str, body: str) -> str:
    md = tmp_path / f"{kind}.md"
    md.write_text(f"# 块测试\n\n## 一节\n\n:::{kind}\n{body}\n:::\n", encoding="utf-8")
    return _render(md2html, md)


def _markup_only(html: str) -> str:
    """去掉 `<style>` / `<script>` 再看。

    CSS 的注释里就写着 `/* ---- :::stats 数字卡 ---- */`,拿"页面里有没有 `:::stats`"
    当"语法标记漏进正文了"的证据,永远会误报。
    """
    return re.sub(r"<style>.*?</style>|<script>.*?</script>", "", html, flags=re.S)


@pytest.mark.parametrize("kind", sorted(BLOCK_SAMPLES))
def test_visual_block_renders_its_component(md2html, tmp_path: Path, kind):
    body, want = BLOCK_SAMPLES[kind]
    html = _render_block_doc(md2html, tmp_path, kind, body)
    assert want in html, f"`:::{kind}` 没渲染出 {want}"
    assert f":::{kind}" not in _markup_only(html), "块的语法标记漏进正文了"


def test_visual_block_with_unknown_type_fails_loudly(md2html, tmp_path: Path):
    """认不出的块类型必须**报错**,不能当成普通段落混过去。

    静默放过的代价:作者以为自己写了一个对比表,读者看到的是一段带 `:::` 的
    奇怪文字 —— 而构建是"成功"的,没人会去看。宁可构建失败。
    """
    md = tmp_path / "bad.md"
    md.write_text("# 块测试\n\n## 一节\n\n:::wat\n- 甲 | 乙\n:::\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不认识的块"):
        md2html.convert(md, md.with_suffix(".html"))


@pytest.mark.parametrize(
    "marker",
    [":::没这个块", ":::STATS", ":::", "::::stats"],
    ids=["中文块名", "大写块名", "孤零零一个收尾", "多打了一个冒号"],
)
def test_a_line_that_starts_with_colons_but_is_not_a_block_fails_loudly(
    md2html, tmp_path: Path, marker
):
    """以 `:::` 开头却不是合法块标记的行,**也要报错**。

    这是最容易静默漏掉的一类:`_extract_blocks` 的正则要求块名是 `[a-z]+`,
    所以 `:::没这个块`、`:::STATS` 这些**根本不匹配**,会被当普通段落原样输出 ——
    页面上出现一行明晃晃的 `:::没这个块`,而构建报的成功。
    """
    md = tmp_path / "odd.md"
    md.write_text(f"# 块测试\n\n## 一节\n\n{marker}\n- 甲 | 乙\n:::\n", encoding="utf-8")
    with pytest.raises(ValueError, match=":::"):
        md2html.convert(md, md.with_suffix(".html"))


def test_stats_tile_without_label_fails_loudly(md2html, tmp_path: Path):
    """`:::stats` 的行是 `- 数值 | 标签`(可以再跟一句说明),少一列要报错。"""
    with pytest.raises(ValueError, match="数值 \\| 标签"):
        _render_block_doc(md2html, tmp_path, "stats", "- 只有一个字段")


def test_chart_with_non_numeric_value_fails_loudly(md2html, tmp_path: Path):
    with pytest.raises(ValueError, match="不是数字"):
        _render_block_doc(md2html, tmp_path, "chart", "- 甲 | 很多")


def test_compare_with_one_column_fails_loudly(md2html, tmp_path: Path):
    """一栏的"对比"没有意义 —— 多半是忘了写分隔用的 `---`。"""
    with pytest.raises(ValueError, match="至少要两栏"):
        _render_block_doc(md2html, tmp_path, "compare", "### 只有一栏\n- 一条")


def test_block_markers_inside_a_fenced_code_block_are_left_alone(md2html, tmp_path: Path):
    """代码块里出现的 `:::stats` 是**例子**,不是要渲染的块。

    这条是给"文档里教别人怎么写 `:::` 块"准备的 —— 扫块的那段代码必须
    跟着 ``` 围栏的状态走,否则它会把示例当成真的块抽走,正文里凭空少一段。
    """
    md = tmp_path / "fenced.md"
    md.write_text(
        "# 围栏\n\n## 一节\n\n```text\n:::stats\n- **1** | 假卡片\n:::\n```\n\n正文结尾。\n",
        encoding="utf-8",
    )
    html = _render(md2html, md)
    assert 'class="stats"' not in html, "代码块里的示例被当成真块渲染了"
    assert ":::stats" in html, "示例本身该原样留在代码块里"
    assert "正文结尾。" in html


def test_mermaid_and_visual_blocks_do_not_collide(md2html, tmp_path: Path):
    """图和 `:::` 块共用一套占位符编号 —— 图不能让块消失,块也不能让图少一张。

    这两者是两趟扫描(先抽 mermaid、再抽 `:::`),编号**必须接着排**。
    以前第二趟从 0 重新开始,于是图和块撞号:图渲染了两遍,而那个 `:::` 块
    **整块消失**,构建却报成功。
    """
    md = tmp_path / "both.md"
    md.write_text(
        "# 两者都有\n\n## 一节\n\n"
        "```mermaid\nflowchart LR\n  A[\"甲\"] --> B[\"乙\"]\n```\n\n"
        ":::note 一句提示\n正文。\n:::\n\n"
        "```mermaid\nflowchart TD\n  C[\"丙\"] --> D[\"丁\"]\n```\n\n"
        ":::cards\n- 卡片 | 说明\n:::\n",
        encoding="utf-8",
    )
    html = _render(md2html, md)
    assert html.count('<figure class="diagram"') == 2, "图少了一张或多了一张"
    assert html.count('class="note"') == 1, "note 块被图挤掉了"
    assert html.count('class="mini"') == 1, "cards 块被图挤掉了"
    assert "ZZPLACE" not in html
