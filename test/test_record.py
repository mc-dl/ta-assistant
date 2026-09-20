"""【记录】入口:助教把一条「问 + 答」写进 FAQ 的那条路。

**这个文件最该防什么。**
它写的不是日志、不是缓存,而是 `常问问题.txt` —— 几十条**手写**材料、
**没有版本库兜底**的唯一副本。所以测试分两半:

- 一半测「写进去的东西对不对」(解析、原子性、立刻可检索);
- 一半测「**该拒的时候真的一字节都没写**」。拒收逻辑写错不会报错,
  只会静默地把不该写的写进生产文件 —— 那是最贵的一种 bug,
  所以每条拒收用例都同时断言"文件没变",而不是只看回执。

第二条主线是**闸门顺序**:`【记录】` 必须走通,且不能被学生转发闸门吞掉。
加这个入口之前,助教发「【记录】…」拿到的是「(非学生消息,未处理)」——
回执看着像"机器人不理我",其实是前缀闸门先把它拦了。所以必须有测试
**直接跑 `process()`**(而不是只测 `record.handle`),否则改一次闸门顺序
就能让功能静默失效,而全部单测照样绿。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src import config
from src.handlers import record
from src.main import process
from src.rag.retriever import FAQRetriever

# 假花名册。**刻意用编造的名字**:测试文件不进生产、但会被助教看,
# 用真名等于把学生信息又抄了一份。学号同理,20230001 是仓库里一直在用的占位。
FAKE_ROSTER = {"20230001": "赵小明", "20230002": "钱小亮"}

_HEADER = """常问问题(FAQ)

维护者:助教 mcdl
格式:每条以 Q: 开头、A: 开头,两条之间空一行(下面第一段是说明,不是条目)

"""
# 真正要用的两条,后面所有断言都围着它们转。
# 挑这两条是因为它们各配一类用例:第 1 条测"完全重复",第 2 条测"很像但不重复"。
_SEED_PAIRS = [
    ("作业漏交了怎么办?", "直接发给我,第二次及以后的补交会酌情扣分。"),
    ("仿真软件 proteus 在哪里下载?", "课程群文件里有个压缩包,解压后按说明装。"),
]

# ── 为什么要 38 条填充,而不是随手写两条 ──────────────────────────
# FAQ 检索用的是 BM25,而 BM25 的分数量级**随语料条数 N 走**(IDF 里带 N)。
# 2026-09-20 实测同一句话的"自命中"分数:
#     真文件(37 条):最低 10.75 / 中位 25.44,37 条全部 ≥ 阈值 4.0;
#     三条目的小样本:1.02 —— **低于阈值**。
# 也就是说,拿三五条写个 toy fixture,`test_recorded_entry_is_immediately_retrievable`
# 会变红,但它红的理由是"IDF 在 3 条语料上太小",不是"记录完检索不到" ——
# 一条**假的**红,比没有测试更糟(会诱人把阈值调低)。
# 所以这里把语料撑到和生产同一个量级,让 4.0 这个阈值有可比的含义。
# 每个主题词只出现一次,DF=1,和真文件里那些稀有词一个量级。
_FILLER_TOPICS = [
    "叠加原理", "戴维南定理", "诺顿定理", "功率因数", "三相电路",
    "磁路分析", "基尔霍夫定律", "节点电压法", "回路电流法", "一阶电路",
    "二阶电路", "正弦稳态", "相量法", "谐振电路", "互感耦合",
    "理想变压器", "二端口网络", "拉普拉斯变换", "网络函数", "频率响应",
    "滤波器", "非正弦周期电流", "对称分量法", "电感的储能", "电容的充放电",
    "时间常数", "品质因数", "阻抗匹配", "最大功率传输", "受控源",
    "运算放大器", "二极管伏安特性", "稳压电路", "电流表内阻", "电压表量程",
    "电桥平衡条件", "星三角变换", "网孔分析",
]
_N = len(_SEED_PAIRS) + len(_FILLER_TOPICS)      # 语料条数,断言里用它算


def _build_faq() -> str:
    """拼一份和生产同量级的 FAQ:带说明头 + 2 条种子 + 38 条填充。"""
    pairs = list(_SEED_PAIRS) + [
        (f"{t}是什么意思?", f"见教材里关于{t}的那一节。") for t in _FILLER_TOPICS
    ]
    return _HEADER + "\n".join(f"Q:\n{q}\nA:\n{a}\n" for q, a in pairs)


@pytest.fixture(autouse=True)
def no_real_gradebook(monkeypatch):
    """默认不给花名册:单测不去读助教机器上那份成绩记分册 xlsx。

    一是慢(openpyxl 读真表),二是**机器相关** —— 那位同学的名字在不在表里
    会让测试时红时绿。要测姓名拦截的用例自己传 `roster=FAKE_ROSTER`。
    和 `test_mine_faq_candidates.py` 里的做法一致。
    """
    monkeypatch.setattr(record, "load_roster", lambda *a, **k: {})


@pytest.fixture
def faq_file(tmp_path, monkeypatch) -> Path:
    """一份和生产同量级的 FAQ(见上面 `_FILLER_TOPICS` 的说明),
    并把 `config.FAQ_PATH` 指过去。

    造**真文件**而不是打桩解析器:这个功能的全部价值就是"文件被改对了",
    换掉解析器等于把要测的东西测没了。
    """
    p = tmp_path / "常问问题.txt"
    p.write_text(_build_faq(), encoding="utf-8")
    monkeypatch.setattr(config, "FAQ_PATH", p, raising=True)
    return p


@pytest.fixture
def logged(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(record, "log_event", lambda ev, **kw: events.append((ev, kw)))
    return events


def _msg(q: str, a: str) -> str:
    return f"问题:{q}\n标准答案:{a}"


def _pairs(path: Path) -> list[tuple[str, str]]:
    return FAQRetriever.from_file(path).qa_pairs


# ─── 解析:助教在手机上会怎么打 ────────────────────────────────
def test_the_plain_two_line_form():
    got = record.parse_record(_msg("Proteus 在哪下载?", "课程群文件里有。"))
    assert got.ok
    assert got.question == "Proteus 在哪下载?"
    assert got.answer == "课程群文件里有。"


def test_standalone_labels_with_multiline_question_and_answer():
    """手机上换行很随意:标记词自己占一行、正文再换行,是最常见的一种。"""
    got = record.parse_record(
        "问题:\nProteus 在哪下载?\n现在还能用吗?\n标准答案:\n课程群文件里有压缩包。\n解压后按说明装。"
    )
    assert got.ok
    assert got.question == "Proteus 在哪下载?\n现在还能用吗?"
    assert got.answer == "课程群文件里有压缩包。\n解压后按说明装。"


@pytest.mark.parametrize("q_label,a_label", [
    ("问题", "标准答案"),
    ("问题", "答案"),          # 少打两个字
    ("问", "答"),              # 再少打两个字
    ("Q", "A"),                # 英文输入法没切回来
])
def test_label_variants(q_label, a_label):
    got = record.parse_record(f"{q_label}:问句\n{a_label}:答复")
    assert got.ok, got.error
    assert (got.question, got.answer) == ("问句", "答复")


@pytest.mark.parametrize("colon", [":", "："])
def test_both_colon_widths(colon):
    """中文输入法下打出全角 `：` 是迟早的事,四个标记词都要认。
    (FAQ 解析器那边为此吃过一次亏,见 retriever._parse_faq 的注释。)
    """
    got = record.parse_record(f"问题{colon}问句\n标准答案{colon}答复")
    assert got.ok, got.error
    assert (got.question, got.answer) == ("问句", "答复")


def test_标准答案_does_not_leave_a_dangling_标准():
    r"""`标准答案:X` 里那两个字必须被整段吃掉,不能剩个「标准」粘在正文前。

    钉的是**行为**,不是实现。写这条时我以为它守的是"正则分支顺序"
    (以为「答案」排在「标准答案」前面就会先匹配上、留下「标准」)——
    变异测试证明那个担心是**多余的**:模式是 `^` 锚定的,
    `^\s*答案\s*[:：]` 在 `标准答案:` 上第 0 个字符就匹配不上,
    把顺序颠倒过来 303 条单测全绿(见 record.py 里那段勘误)。
    所以这条测试的真正价值是"答案正文不许被标记词污染"这个可观测结论,
    至于它靠什么实现,交给改动的人自己决定。
    """
    got = record.parse_record("问题:作业交给谁\n标准答案:交给学委")
    assert got.answer == "交给学委", f"答案被前缀污染了:{got.answer!r}"


def test_blank_lines_inside_the_body_are_dropped():
    """正文里的空行不写进 FAQ。

    `_parse_faq` 用「空行 + 下一条 Q」判断条目结束,留着空行当下不会解析错,
    但下一个条目被人手工插进来时,空行就成了歧义的来源 —— 写进去的东西
    尽量不带多余结构。
    """
    got = record.parse_record("问题:第一行\n\n第二行\n标准答案:A\n\nB")
    assert got.question == "第一行\n第二行"
    assert got.answer == "A\nB"


# ─── 解析:拿不准的一律拒,别猜 ────────────────────────────────
def test_missing_answer_is_refused_with_the_usage():
    got = record.parse_record("问题:Proteus 在哪下载?")
    assert not got.ok
    assert "标准答案" in got.error and "【记录】问题:" in got.error


def test_missing_question_is_refused():
    got = record.parse_record("标准答案:交给学委")
    assert not got.ok
    assert "问题" in got.error


@pytest.mark.parametrize("body", [
    "问题:\n标准答案:交给学委",       # 问题标了但后面是空的
    "问题:交给谁\n标准答案:",          # 答案标了但后面是空的
])
def test_empty_side_is_refused(body):
    got = record.parse_record(body)
    assert not got.ok and "空的" in got.error


def test_two_entries_in_one_message_are_refused():
    """一次只记一条。

    不猜的理由:两条一起进来时,"哪条算问题、哪条算答案"没有唯一解 ——
    而猜错的后果是一条内容错位的 FAQ 被**安静地**写进去,
    学生问 A 拿到 B 的答案,谁都不会发现。
    """
    got = record.parse_record("问题:A\n标准答案:B\n问题:C\n标准答案:D")
    assert not got.ok
    assert "一次只能记录一条" in got.error
    assert "第 3 行" in got.error, "要说清是哪一行,不然助教得自己数"
    assert got.question == "" and got.answer == "", "拒收时不该给出半成品正文"


def test_a_label_line_inside_the_answer_is_refused():
    """答案正文里出现行首的 `A:` 会把这条**截成两半**。

    FAQ 文件就是靠行首的 `Q:`/`A:` 划分条目的(见 `retriever._parse_faq`),
    真写进去以后,后一半会被解析器当成"下一条的开头",而它又没有答案
    → 整段被静默丢掉。这是唯一一种"写进去了但内容会消失"的情况。
    """
    got = record.parse_record("问题:A\n标准答案:第一行\nA:第二行")
    assert not got.ok
    assert "截成两半" in got.error


def test_leading_chatter_before_the_labels_is_tolerated():
    """助教顺口加一句"这条帮我记一下"不该报错 —— 手机上多打几个字太正常了。"""
    got = record.parse_record("这条帮我记一下\n问题:A\n标准答案:B")
    assert got.ok and got.question == "A"


# ─── 拒收:个人信息(FAQ 是发给学生的)────────────────────────
def _assert_untouched(path: Path, before: bytes):
    assert path.read_bytes() == before, "拒收了却还是动了文件"


def test_student_id_is_refused_and_nothing_is_written(faq_file):
    before = faq_file.read_bytes()
    out = record.handle(_msg("我是20230001,作业交到哪?", "交给学委"), roster=FAKE_ROSTER)
    assert "❌" in out["reply"] and "学号" in out["reply"]
    assert "20230001" not in out["reply"], "回执里不该回显那个学号"
    _assert_untouched(faq_file, before)


def test_student_id_in_the_answer_is_refused(faq_file):
    before = faq_file.read_bytes()
    out = record.handle(_msg("作业交到哪?", "名单见 20230001 那一行"), roster=FAKE_ROSTER)
    assert "❌" in out["reply"]
    _assert_untouched(faq_file, before)


def test_roster_name_is_refused(faq_file):
    before = faq_file.read_bytes()
    out = record.handle(_msg("赵小明问作业交到哪", "交给学委"), roster=FAKE_ROSTER)
    assert "❌" in out["reply"] and "姓名" in out["reply"]
    assert "赵小明" not in out["reply"], "回执里不该把学生姓名再抄一遍"
    _assert_untouched(faq_file, before)


def test_short_numbers_are_not_mistaken_for_a_student_id(faq_file):
    """**反向用例,和上面几条一样重要。**

    拦截写宽了,这个入口就没法用了 —— 题号(1.36)、页码(188)、
    第几次作业(第2次)都是短数字,一律拦掉等于"记什么都记不进去"。
    所以钉住:7 位以下连续数字放行,8 位以上才拦。
    """
    out = record.handle(
        _msg("第2次作业的 1.36 题在哪一页?P188 那个公式", "在教材第188页"), roster=FAKE_ROSTER
    )
    assert "✅" in out["reply"], out["reply"]
    assert len(_pairs(faq_file)) == _N + 1


def test_a_roster_name_that_is_not_present_is_fine(faq_file):
    """花名册里没有的名字(比如张老师)不拦 —— 拦的是**学生**姓名。"""
    out = record.handle(_msg("张老师说的补交规则是?", "截止后 7 天内登记"), roster=FAKE_ROSTER)
    assert "✅" in out["reply"], out["reply"]


def test_having_no_roster_is_said_out_loud(faq_file):
    """花名册读不到时**要在回执里说出来**:那时候姓名检查是关着的,
    助教以为有保护、其实没有,比明说"没保护"危险得多。
    """
    out = record.handle(_msg("Proteus 在哪下载?", "课程群文件里有"), roster={})
    assert "✅" in out["reply"]
    assert "花名册没读到" in out["reply"]


# ─── 拒收:长度 ────────────────────────────────────────────────
def test_over_long_question_is_refused(faq_file):
    before = faq_file.read_bytes()
    out = record.handle(_msg("电" * 201, "答复"), roster=FAKE_ROSTER)
    assert "❌" in out["reply"] and "太长" in out["reply"]
    _assert_untouched(faq_file, before)


def test_over_long_answer_is_refused(faq_file):
    before = faq_file.read_bytes()
    out = record.handle(_msg("问句", "答" * 1001), roster=FAKE_ROSTER)
    assert "❌" in out["reply"] and "太长" in out["reply"]
    _assert_untouched(faq_file, before)


def test_exactly_at_the_cap_is_allowed(faq_file):
    """边界含不含要钉住,不然"差一个字被拒"这种怪事没人说得清。"""
    out = record.handle(_msg("电" * 200, "答" * 1000), roster=FAKE_ROSTER)
    assert "✅" in out["reply"], out["reply"]


# ─── 拒收:重复 ────────────────────────────────────────────────
@pytest.mark.parametrize("again", [
    "作业漏交了怎么办?",
     "作业漏交了怎么办？",          # 全角问号
    " 作业漏交了怎么办? ",         # 前后空格
    "作业漏交了，怎么办",           # 换个标点
])
def test_duplicate_question_is_refused(faq_file, again):
    """重复条目会互相抢分,还可能给出两个不一样的答案 —— 拒掉。

    判定要**忽略标点和空白**:助教第二次打的时候标点、空格、全半角
    几乎不可能和第一次一模一样,照字面比等于没比。
    """
    before = faq_file.read_bytes()
    out = record.handle(_msg(again, "再答一次"), roster=FAKE_ROSTER)
    assert "❌" in out["reply"] and "已经有了" in out["reply"]
    n = [q for q, _a in _pairs(faq_file)].index("作业漏交了怎么办?") + 1
    assert f"第 {n} 条" in out["reply"], "要说清是库里第几条,方便他去找"
    _assert_untouched(faq_file, before)


def test_nearly_duplicate_is_warned_but_still_written(faq_file):
    """**很像**不等于重复:学生两种说法都可能问,两种都该收。

    但要在回执里提醒一句,让助教自己判断 —— 只拒"完全一样"的,
    其余交给人看,不替他做减法。
    """
    out = record.handle(_msg("proteus 下载地址在哪", "课程群文件里"), roster=FAKE_ROSTER)
    assert "✅" in out["reply"], out["reply"]
    assert "很像的" in out["reply"]
    assert len(_pairs(faq_file)) == _N + 1, "提醒归提醒,该写的还是要写进去"


# ─── 写入本身 ──────────────────────────────────────────────────
def test_success_reports_what_can_now_be_retrieved_and_where(faq_file):
    out = record.handle(_msg("Proteus 在哪下载?", "课程群文件里有"), roster=FAKE_ROSTER)
    reply = out["reply"]
    assert out["type"] == "record"
    assert "✅" in reply and f"{_N + 1} 条" in reply
    assert "Proteus 在哪下载?" in reply, "回执要回显问句,让助教能核对"
    assert str(faq_file) in reply, "回执要带上文件路径 —— 路径配错时这是唯一线索"


def test_the_original_file_is_only_appended_to(faq_file):
    """**原有内容一个字节都不许动。**

    这份文件是手写的,没有版本库;重排、统一换行、顺手格式化
    都会让助教下一次 `git diff`(或任何文本比较)变成一团噪声,
    严重的话还会把他在别处打开的编辑器里的修改冲掉。
    """
    before = faq_file.read_bytes()
    record.handle(_msg("Proteus 在哪下载?", "课程群文件里有"), roster=FAKE_ROSTER)
    after = faq_file.read_bytes()
    assert after.startswith(before), "新内容只能追加在末尾,不能重排前面的东西"
    assert before in after


def test_a_new_entry_is_separated_by_a_blank_line(faq_file):
    record.handle(_msg("Proteus 在哪下载?", "课程群文件里有"), roster=FAKE_ROSTER)
    text = faq_file.read_text(encoding="utf-8")
    assert "\n\nQ:\nProteus 在哪下载?\nA:\n课程群文件里有\n" in text


def test_no_temp_file_is_left_behind(faq_file):
    """原子替换用了一个临时文件,写完必须收拾干净 ——
    不然那个目录里会慢慢堆满 `.常问问题.txt.<pid>.tmp`,而它们**看起来像 FAQ**。
    """
    record.handle(_msg("Proteus 在哪下载?", "课程群文件里有"), roster=FAKE_ROSTER)
    # 只看 `*.tmp`:这个 tmp 目录里同时还住着 `isolated_logs` 指过来的 `logs/`,
    # 断言"目录里只有 FAQ"会把日志一起算进来。
    leftovers = [p.name for p in faq_file.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"临时文件没收拾干净:{leftovers}"


def test_the_write_really_goes_through_a_temp_file_and_replace(faq_file, monkeypatch):
    """**原子性的机制要直接钉住,不能只靠"没留下垃圾文件"。**

    这条是变异测试逼出来的:上面那条 `test_no_temp_file_is_left_behind`
    抓不住"直接往原文件里写"这种改法 —— 那种改法也不留临时文件,照样全绿。
    可 FAQ 是手写的唯一副本:写到一半崩了,留下的是**半条记录**,
    整份文件从此解析错乱,而且看不出是什么时候坏的。

    所以这里盯着 `os.replace` 到底有没有被调用:只有"先写临时文件、
    再原子改名"这条路,才能保证文件要么是旧的完整版、要么是新的完整版。
    """
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(record.os, "replace", spy)

    record.handle(_msg("课程群怎么加?", "让学委拉你进群。"), roster=FAKE_ROSTER)

    assert len(calls) == 1, f"期望恰好一次原子替换,实际:{calls}"
    src, dst = calls[0]
    assert src.name.endswith(".tmp") and src.parent == faq_file.parent, \
        f"应该先往同目录下的临时文件写(跨文件系统 rename 不是原子的):{src}"
    assert dst == faq_file


def test_crlf_and_bom_of_the_original_are_preserved(tmp_path, monkeypatch):
    """助教是在 Windows 记事本里编这个文件的:CRLF + BOM 都要原样保住。

    混进 LF 会让整份文件一半 CRLF 一半 LF,文本比较工具和 `git diff`
    都会显示"整个文件都改了";丢了 BOM 则老记事本会认成 ANSI,中文全乱。
    """
    p = tmp_path / "常问问题.txt"
    p.write_bytes("﻿Q:\r\n旧问题\r\nA:\r\n旧答案\r\n".encode("utf-8"))
    monkeypatch.setattr(config, "FAQ_PATH", p, raising=True)

    record.handle(_msg("新问题", "新答案"), roster=FAKE_ROSTER)

    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM 丢了"
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n"), "混进了 LF"
    assert "旧问题" in raw.decode("utf-8-sig") and "新问题" in raw.decode("utf-8-sig")


def test_recorded_entry_is_immediately_retrievable(faq_file):
    """**这条是整个功能的验收判据。**

    助教记一条 FAQ,图的是"下一个学生问到时它就能用"。所以写完必须**当场
    用回答学生的那条检索路径**捞一遍 —— 用真 `FAQRetriever` 读文件、
    真的分词打分,而不是断言"我调用过写入函数"。

    它同时钉住一件事:FAQ 检索**每次现读文件、没有常驻缓存**
    (见 `FAQRetriever.from_file`),所以记录之后**不需要重建索引**。
    哪天有人给 FAQRetriever 加缓存,这条会红 —— 那是对的,
    那时候记录入口就得负责让缓存失效。
    """
    # 刻意挑一个**库里没有的话题**:如果记的问题和种子条目撞车,
    # 这条就变成在测"两条相似条目谁分高",而不是"新记的那条能不能被捞到"。
    record.handle(_msg("课程群怎么加?", "让学委拉你进群。"), roster=FAKE_ROSTER)

    r = FAQRetriever.from_file(faq_file)
    hits = r.search("课程群怎么加?")
    assert hits, "刚记的问题检索不到 —— 那记录这个功能就没意义了"
    assert hits[0]["question"] == "课程群怎么加?"
    assert hits[0]["score"] >= config.FAQ_HIT_THRESHOLD, (
        f"分数 {hits[0]['score']:.2f} 低于阈值 {config.FAQ_HIT_THRESHOLD}:"
        f"记进去了,但学生问同一句话时它不会走 FAQ 通路"
    )


# ─── 失败与回滚 ────────────────────────────────────────────────
def test_missing_faq_file_is_refused_and_not_created(tmp_path, monkeypatch):
    """FAQ 文件不在时**拒绝**,而不是"顺手新建一个"。

    路径配错时自动建文件 = 系统里出现第二份 FAQ:助教以为记进去了,
    学生那边一条都搜不到。静默分裂比报错难查得多,所以宁可什么都不做,
    并把这个路径明明白白打给助教看。
    """
    missing = tmp_path / "不存在的目录" / "常问问题.txt"
    monkeypatch.setattr(config, "FAQ_PATH", missing, raising=True)

    out = record.handle(_msg("问句", "答复"), roster=FAKE_ROSTER)

    assert "❌" in out["reply"] and "不存在" in out["reply"]
    assert str(missing) in out["reply"]
    assert not missing.exists(), "说不建就不建"
    assert not missing.parent.exists(), "连目录都不该建"


def test_rollback_when_the_written_entry_cannot_be_read_back(faq_file, monkeypatch):
    """写完回读一遍,读不回来就**按原字节回滚**。

    什么情况下会读到不回来:那份文件的结构已经和解析器对不上
    (比如有人手工插了一条格式坏掉的条目)。这时候最坏的做法是"写了一半
    又报警" —— 文件被改了、内容却是坏的。原则是"要么成功、要么什么都没有"。
    """
    before = faq_file.read_bytes()
    real = record.FAQRetriever
    calls = {"n": 0}

    class _BlindOnReread:
        """第一次(写之前读库)照实走,第二次(写之后回读)假装解析不出东西。"""

        @classmethod
        def from_file(cls, path):
            calls["n"] += 1
            return real([]) if calls["n"] >= 2 else real.from_file(path)

    monkeypatch.setattr(record, "FAQRetriever", _BlindOnReread)

    out = record.handle(_msg("新问题", "新答案"), roster=FAKE_ROSTER)

    assert "❌" in out["reply"] and "回滚" in out["reply"]
    assert faq_file.read_bytes() == before, "回滚没做干净,文件被留下了改动"
    assert calls["n"] == 2


def test_empty_command_shows_the_usage(faq_file):
    before = faq_file.read_bytes()
    out = record.handle("", roster=FAKE_ROSTER)
    assert "❌" in out["reply"] and "【记录】问题:" in out["reply"]
    _assert_untouched(faq_file, before)


def test_strip_prefix_is_idempotent_for_the_handler(faq_file):
    """`handle` 带不带前缀都能用。

    因为 `main.process()` 会先把前缀剥掉再传进来,而测试/脚本常常直接
    调 `handle`。两边都认,就不会出现"测试里过了、线上不认"这种偏差。
    """
    out = record.handle("【记录】" + _msg("Proteus 在哪下载?", "课程群文件里有"),
                        roster=FAKE_ROSTER)
    assert "✅" in out["reply"], out["reply"]


def test_strip_prefix_tolerates_leading_whitespace():
    matched, body = record.strip_prefix("\n  【记录】问题:甲\n标准答案:乙")
    assert matched and body.startswith("问题:甲")
    assert record.strip_prefix("【转发学生提问】这个多少钱")[0] is False


# ─── 日志:成功的要留痕,被拒的不能留正文 ─────────────────────
def test_a_successful_record_logs_the_question(logged, faq_file):
    record.handle(_msg("Proteus 在哪下载?", "课程群文件里有"), roster=FAKE_ROSTER)
    ev, kw = logged[-1]
    assert ev == "record" and kw["ok"] is True
    assert kw["q"] == "Proteus 在哪下载?"
    assert kw["n_entries"] == _N + 1 and kw["added"] == 1
    assert "a_len" in kw and "answer" not in kw, "答案正文不进日志,只记长度"


def test_a_refusal_logs_the_reason_but_not_the_text(logged, faq_file):
    """因为可能带个人信息才拒的,就**不能**再把那段原文抄进日志一份。

    否则"拒收"只是没进 FAQ,信息照样落到了另一个文件里,而助教
    完全不知道 —— 拒收该是"没有发生",不是"换个地方存起来"。
    """
    record.handle(_msg("我是20230001,作业交到哪?", "交给学委"), roster=FAKE_ROSTER)
    ev, kw = logged[-1]
    assert ev == "record" and kw["ok"] is False
    assert "学号" in kw["reason"]
    blob = repr(kw)
    assert "20230001" not in blob and "作业交到哪" not in blob
    assert "q" not in kw, "被拒的记录不该落问句正文"


# ─── 闸门:这条命令必须真的能走到 handler ─────────────────────
def test_process_routes_record_before_the_student_gate(faq_file):
    """**端到端**:助教发的这句话,必须真的走到记录入口。

    这条是加这个功能之前**不成立**的那件事:那时 `process()` 只有一道
    "是不是学生转发"的闸门,`【记录】…` 不带转发前缀 → 判成
    「(非学生消息,未处理)」,功能看着在、其实够不着。
    所以这里断言的不是"类型对不对",而是**文件真的多了一条**。
    """
    out = process("【记录】问题:Proteus 在哪下载?\n标准答案:课程群文件里有")
    assert out["type"] == "record", out
    assert "✅" in out["reply"]
    assert len(_pairs(faq_file)) == _N + 1
    assert any("Proteus 在哪下载?" == q for q, _a in _pairs(faq_file))


def test_process_still_skips_messages_without_any_prefix(faq_file):
    """反向:别为了加这条通路,把"不带前缀的一律不处理"给弄丢了 ——
    那是防止机器人乱答群里闲聊的第一道闸门。
    """
    before = faq_file.read_bytes()
    out = process("老师这个作业什么时候交")
    assert out["type"] == "skip"
    _assert_untouched(faq_file, before)


def test_process_still_routes_student_forwarding_to_the_qa_path(faq_file):
    """`【记录】` 那一支不能把学生转发也吞掉:两条通路互不干扰。"""
    out = process("【转发学生提问】什么是叠加原理")
    assert out["type"] == "question", out


def test_a_full_record_never_touches_the_real_faq():
    """**隔离哨兵:单测绝不许写进助教那份手写的常问问题.txt。**

    这是 `conftest.isolated_faq` 的看门测试。顺序是刻意的:
    **先断言路径已经被指走,再去读真文件**。反过来的话,哪天有人把那个
    autouse fixture 删了,这条测试自己就会变成"往生产 FAQ 里写一条"的凶手 ——
    守卫测试不能比它守的那个洞更危险。

    (同类事故这个项目已经有过一次:`data/logs` 被单测灌了 96% 的假问句,
    见 `test/conftest.py::isolated_logs`。区别是日志能归档重来,这份 FAQ 不能。)
    """
    real = config.WINDOWS_ROOT / "常问问题.txt"
    assert config.FAQ_PATH != real, (
        f"单测里的 FAQ 路径指到了生产文件 {real} —— "
        f"conftest.isolated_faq 是不是被删了?继续跑下去会改写助教的数据。"
    )

    before = real.read_bytes() if real.exists() else None
    process("【记录】问题:隔离哨兵,不该落到生产文件里\n标准答案:如果这条出现在真文件里,说明隔离坏了")
    after = real.read_bytes() if real.exists() else None
    assert before == after
