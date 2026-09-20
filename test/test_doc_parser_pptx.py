# -*- coding: utf-8 -*-
"""PPTX 解析的回归测试(`src/utils/doc_parser._parse_pptx`)。

为什么要专门测 pptx(2026-09-20):
张曰理老师的「错误集锦」课件是 pptx,里面记着**教材哪一页印错了、哪道题不用做**,
是很实用的一份资料。但第一版解析只遍历顶层形状,**只拿到页标题**(
要点全在"组合形状"(group)里 —— 作者排版时把它们打包了)。
所以这里钉住三件事:

  1. 顶层文本框能取到;
  2. **表格**能取到(错误集锦的"错误/正确"对照就是表格排的);
  3. **组合形状要递归进去**(下面用假对象直接测递归函数,不依赖 python-pptx
     的 group 构造 API —— 那个 API 各版本差异大,拿它当测试前提不稳)。

另:页面要带 `[第N页]` 标记,这样回答学生"教材 P188"时能对上页码。
"""
import pytest

# 解析器本身依赖 python-pptx,测试也直接依赖它。缺了就该红,不该静默跳过 ——
# 没有它,错误集锦课件会退化成"只有页标题"。
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.utils.doc_parser import _parse_pptx, _pptx_shape_text


# ─── 假对象:只实现 _pptx_shape_text 用到的那几个属性 ────────────────

class FakeTextFrame:
    def __init__(self, text):
        self.text = text


class FakeShape:
    """普通形状(文本框)。"""
    def __init__(self, text):
        self.has_text_frame = True
        self.has_table = False
        self.shape_type = MSO_SHAPE_TYPE.TEXT_BOX
        self.text_frame = FakeTextFrame(text)


class FakeCell:
    def __init__(self, text):
        self.text = text


class FakeRow:
    """python-pptx 里 `table.rows` 迭代出来的是行对象,行上的单元格在 `.cells`。"""
    def __init__(self, texts):
        self.cells = [FakeCell(t) for t in texts]


class FakeTable:
    def __init__(self, rows):
        self.rows = [FakeRow(r) for r in rows]


class FakeTableShape:
    has_text_frame = False
    has_table = True
    shape_type = MSO_SHAPE_TYPE.TABLE

    def __init__(self, rows):
        self.table = FakeTable(rows)


class FakeGroup:
    shape_type = MSO_SHAPE_TYPE.GROUP

    def __init__(self, children):
        self.shapes = children


def test_group_shapes_are_walked_recursively():
    """组合形状里的文字**必须**取到 —— 这是当初漏掉的正是这块。"""
    group = FakeGroup([FakeShape("页码P188, 此题错误，不用做")])
    assert _pptx_shape_text([group]) == ["页码P188, 此题错误，不用做"]


def test_nested_groups_are_walked():
    """组里还有组(课件里真的有两层)。"""
    inner = FakeGroup([FakeShape("内层要点")])
    outer = FakeGroup([FakeShape("外层要点"), inner])
    assert _pptx_shape_text([outer]) == ["外层要点", "内层要点"]


def test_table_rows_are_joined_with_pipes():
    """表格按行拼成 `A | B`,错误集锦的对照表靠这个。"""
    shape = FakeTableShape([["错误", "正确"], ["P9", "115 V"]])
    assert _pptx_shape_text([shape]) == ["错误 | 正确", "P9 | 115 V"]


def test_empty_and_broken_shapes_are_skipped_not_raised():
    """空文本框/坏形状只跳过,不能让整份课件解析失败。"""
    class Broken:
        shape_type = 1

        @property
        def has_text_frame(self):
            raise RuntimeError("模拟 python-pptx 的怪形状")

    shapes = [FakeShape("   "), FakeShape("有效文字"), Broken()]
    assert _pptx_shape_text(shapes) == ["有效文字"]


# ─── 端到端:真造一个 pptx ────────────────────────────────────────────

@pytest.fixture
def sample_pptx(tmp_path):
    """用 python-pptx 现造一份小课件(文本框 + 表格 + 备注)。"""
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])   # 6 = 空白版式

    box = slide.shapes.add_textbox(0, 0, 100, 100)
    box.text_frame.text = "《电路基础》错误集锦-第6版"

    rows, cols = 2, 2
    table = slide.shapes.add_table(rows, cols, 0, 0, 100, 100).table
    table.cell(0, 0).text = "页码"
    table.cell(0, 1).text = "正确写法"
    table.cell(1, 0).text = "P9"
    table.cell(1, 1).text = "115 V"

    slide.notes_slide.notes_text_frame.text = "讲课时口头提到的补充"

    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    return path


def test_parse_pptx_returns_text_table_and_notes(sample_pptx):
    text = _parse_pptx(sample_pptx)
    assert "[第1页]" in text                 # 页码标记,便于回答"教材 P188"
    assert "错误集锦" in text                # 文本框
    assert "P9 | 115 V" in text              # 表格按行拼
    assert "讲课时口头提到的补充" in text     # 备注也带上


def test_parse_document_dispatches_pptx(sample_pptx):
    """parse_document 必须按扩展名分派到 pptx,别掉进"返回空字符串"的兜底。"""
    from src.utils.doc_parser import parse_document
    assert "错误集锦" in parse_document(sample_pptx)


def test_parse_document_on_garbage_returns_empty(tmp_path):
    """坏文件由 parse_document 兜住、返回空串(上层靠空串判断"这份资料读不出来")。"""
    from src.utils.doc_parser import parse_document
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a real pptx")
    assert parse_document(bad) == ""
