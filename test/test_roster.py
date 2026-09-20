"""花名册读取:两种真实版式、失败要给出原因、以及那个"读不到还不报错"的坑。

**这个文件存在的理由,是原来那套坐标写死了。**
旧代码按「表头在第 5 行、数据从第 7 行、学号在 B 列、姓名在 C 列」读,那只对
**成绩记分册一种版式**成立。花名册每学期都换(雨课堂导出、教务导出、记分册…),
版式一变,写死的坐标不会报错,只会**安静地读成一份空名单** —— 于是"学号/姓名校验"
停摆了好几个月,补交表里 398 行校验状态全是「名单缺失」,没有任何一处会红。

所以两条线一起钉:
  · 版式换了也能读(表头文字自动定位);
  · 读不出来必须**说得出为什么**(`scan_roster` 的 reason),而不是返回空字典了事。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import openpyxl
import pytest

from src.utils import doc_parser
from src.utils.excel_ops import load_roster, scan_roster


def _write_xlsx(path: Path, rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(str(path))
    return path


def _set_dimension(path: Path, ref: str | None) -> Path:
    """把 xlsx 里的 `<dimension>` 改成 `ref`(传 None = 整条删掉)。

    这不是无中生有。**真实的成绩记分册里写的就是 `<dimension ref="A1"/>`** ——
    一个错的范围声明(它明明有 146 行 17 列)。openpyxl 的只读模式只信这个标签、
    也不校验,于是把 140 个学生的工作簿读成 1 行 1 列。
    注意"没有标签"和"标签写错"是**两种不同的坏法**,openpyxl 对它们的反应也不同
    (见 test_openpyxl_read_only_trusts_a_wrong_dimension),所以两种都测。
    """
    tmp = path.with_name(path.stem + ".dim.xlsx")
    with zipfile.ZipFile(path) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                tag = b"" if ref is None else b'<dimension ref="%s"/>' % ref.encode()
                data = re.sub(rb"<dimension[^>]*/>", tag, data)
            zout.writestr(item, data)
    tmp.replace(path)
    return path


# ─── 两种真实版式 ─────────────────────────────────────────────

def test_yuketang_export_layout(tmp_path):
    """雨课堂「试卷-数据表」导出:第 1 行是标题,第 2 行是表头,学号/姓名在最左两列。"""
    p = _write_xlsx(tmp_path / "雨课堂.xlsx", [
        ["第二章作业-试卷-2026-09-20 19:15:10（若考试设置了题目乱序，…）"],
        ["学号", "姓名", "进入考试时间", "得分 (总:10.0分)"],
        ["2530123456", "张三", "2026-09-18 08:00", 10],
        ["2530654321", "李四", None, 8],
        ["2630111111", "王五", None, None],
    ])
    scan = scan_roster(p)
    assert scan.ok and scan.count == 3
    assert (scan.header_row, scan.id_col, scan.name_col) == (2, 1, 2)
    assert scan.roster["2530123456"] == "张三"


def test_gradebook_layout(tmp_path):
    """老版成绩记分册:前 4 行是抬头,第 5 行是表头(学号在 B、姓名在 C),第 6 行还有一层子表头。"""
    p = _write_xlsx(tmp_path / "记分册.xlsx", [
        ["中山大学2025-2学年学期 学生考勤签名、平时计分表"],
        ["教学班号：202526376  课程：数字电路与逻辑设计实验（一）"],
        ["学时：36.0  上课时间地点：…"],
        ["任课教师： 张东             教师签名："],
        ["序号", "学号", "姓名", "行政班", "性别", "考勤、平时作业", None, None],
        [None, None, None, None, None, 1, 2, 3],          # 子表头,必须被跳过
        [1, "20230001", "张三", "电信1班", "女"],
        [2, "20230002", "李四", "电信1班", "男"],
    ])
    scan = scan_roster(p)
    assert scan.ok and scan.count == 2
    assert (scan.header_row, scan.id_col, scan.name_col) == (5, 2, 3)
    assert scan.roster["20230002"] == "李四"


# ─── 失败必须说得出原因 ───────────────────────────────────────

def test_missing_file_explains_itself(tmp_path):
    scan = scan_roster(tmp_path / "根本没有这个文件.xlsx")
    assert not scan.ok and scan.count == 0
    assert "不存在" in scan.reason


def test_file_without_any_header_explains_itself(tmp_path):
    p = _write_xlsx(tmp_path / "不是名单.xlsx", [
        ["实验一 基尔霍夫定律", "一、实验目的"],
        ["1. 掌握节点电流法", "2. 掌握回路电压法"],
    ])
    scan = scan_roster(p)
    assert not scan.ok
    assert "表头" in scan.reason
    assert "学号" in scan.reason       # 得说清在找什么,不然助教不知道该改哪儿


def test_load_roster_returns_empty_dict_instead_of_raising(tmp_path):
    """补交登记不该因为名单读不出来就整个失败 —— 但**空字典是有代价的**,见文件头。"""
    assert load_roster(tmp_path / "没有.xlsx") == {}


# ─── 单元格值的归一化 ─────────────────────────────────────────

def test_numeric_and_float_student_ids_are_normalized(tmp_path):
    """学号是数字时 openpyxl 读成 int,位数再多就读成 float —— 不能带出 ".0"。"""
    p = _write_xlsx(tmp_path / "数字学号.xlsx", [
        ["学号", "姓名"],
        [2530123456, "张三"],          # 整数形式写进去
        [2530123457.0, "李四"],        # 浮点形式写进去
    ])
    roster = load_roster(p)
    assert set(roster) == {"2530123456", "2530123457"}
    assert all("." not in k for k in roster)


def test_rows_with_bad_id_or_missing_name_are_skipped(tmp_path):
    p = _write_xlsx(tmp_path / "杂行.xlsx", [
        ["学号", "姓名"],
        ["20230001", "张三"],
        ["123", "学号太短"],           # < 6 位
        ["2023000X", "学号含字母"],
        ["20230002", None],            # 没有姓名
        [None, None],                  # 空行:不算异常
        ["20230003", "王五"],
    ])
    scan = scan_roster(p)
    assert scan.count == 2
    assert scan.skipped_rows == 3
    assert set(scan.roster) == {"20230001", "20230003"}


def test_conflicting_duplicate_ids_are_counted_not_silently_overwritten(tmp_path):
    p = _write_xlsx(tmp_path / "重号.xlsx", [
        ["学号", "姓名"],
        ["20230001", "张三"],
        ["20230001", "张四"],          # 同一个学号两个名字:保留先出现的,但要计数
        ["20230001", "张三"],          # 完全重复:不算异常
    ])
    scan = scan_roster(p)
    assert scan.count == 1
    assert scan.roster["20230001"] == "张三"
    assert scan.duplicates == 1


# ─── 那个真正的坑 ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "ref", ["A1", None], ids=["dimension-wrong(记分册那种)", "dimension-missing"]
)
def test_a_lying_dimension_tag_does_not_cost_us_rows(tmp_path, ref):
    """**回归钉子**:`<dimension>` 写错或没有,都必须照样读全。

    成绩记分册就长这样。旧代码 `read_only=True` 把它读成 1 行 1 列,还不报错 ——
    和"路径配错"叠在一起,让学号校验静默停摆了几个月。
    """
    p = _write_xlsx(tmp_path / "坏dimension.xlsx", [
        ["中山大学2025-2学年学期 学生考勤签名、平时计分表"],
        ["教学班号：202526376"],
        ["序号", "学号", "姓名"],
        [1, "20230001", "张三"],
        [2, "20230002", "李四"],
        [3, "20230003", "王五"],
    ])
    _set_dimension(p, ref)

    scan = scan_roster(p)
    assert scan.ok, scan.reason
    assert scan.count == 3, "<dimension> 撒谎时把行读漏了 —— 正是这个 bug 的复现"

    # 同一个坑在语料解析那边也在:解析器原来也是 read_only,
    # 结果整份成绩记分册进索引时只贡献了标题那一格
    text = doc_parser.parse_document(p)
    assert "20230001" in text or "张三" in text, \
        "doc_parser 也没读全这份表格(语料会静默缩水)"


def test_openpyxl_read_only_trusts_a_wrong_dimension(tmp_path):
    """把上面那条的**原因**也钉住:只读模式只信 `<dimension>`,而它可以撒谎。

    这条不是测我们的代码,是测"我们为什么不能图省事用 read_only"。
    如果哪天它红了,说明 openpyxl 改了行为,`excel_ops._load()` 和
    `doc_parser._parse_xlsx()` 里的注释就该跟着改。

    两种坏法的表现还不一样:
      · `ref="A1"`(记分册那种)→ 报 max_row=1,于是**静默地只读一行**;
      · 整条没有 → 报 max_row=None,连"有几行"都不知道。
    两种都会让 `iter_rows` 读不到学生,但只有前者看起来像"读到了"。
    """
    base = [
        ["抬头"], ["抬头"], ["学号", "姓名"],
        ["20230001", "张三"], ["20230002", "李四"],
    ]
    wrong = _set_dimension(_write_xlsx(tmp_path / "写错.xlsx", base), "A1")
    missing = _set_dimension(_write_xlsx(tmp_path / "没有.xlsx", base), None)

    ro_wrong = openpyxl.load_workbook(str(wrong), data_only=True, read_only=True)
    assert ro_wrong.active.max_row == 1, "openpyxl 的行为变了?去改 _load() 的注释"

    ro_missing = openpyxl.load_workbook(str(missing), data_only=True, read_only=True)
    assert ro_missing.active.max_row is None

    # 普通模式下两份都能读全 —— 这就是修法
    for p in (wrong, missing):
        full = openpyxl.load_workbook(str(p), data_only=True, read_only=False)
        assert full.active.max_row == 5
