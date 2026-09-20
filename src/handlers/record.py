"""【记录】入口:助教在微信里说一句话,就把「问 + 答」写进 FAQ。

## 为什么要有这个入口

FAQ(`常问问题.txt`)是学生提问**最先撞上**的一层,而它此前唯一的维护方式是
"打开电脑、找到那个 txt、想一下格式、手打 `Q:` / `A:`、存盘"。步骤一多,
这件事就不会发生 —— 于是助教刚在群里答完的那句话烂在聊天记录里,
下一个学生问同样的问题,还得再答一遍。

所以入口做成**在微信里直接说**:

    【记录】问题:仿真软件 Proteus 在哪下载?
    标准答案:课程群文件里有个压缩包,解压后按里面的说明装。

`src/main.py` 在**学生转发闸门之前**认这个前缀(顺序不能反,见那边的注释),
写完立刻生效 —— 下一个学生问到时,`FAQRetriever` 会从文件里重新读一遍
(它没有常驻缓存),所以不需要重建索引。这一点**不靠"我记得"来保证**,
有测试盯着:`test_record.py::test_recorded_entry_is_immediately_retrievable`。

## 四条约束,都是先想清楚"错了会怎样"才定的

1. **FAQ 是要发给学生的**,不能带学生个人信息。学号(8 位以上连续数字)和
   花名册上的姓名一律**拒收**,而不是悄悄抹掉 —— 抹掉等于替助教改字,
   而他看不到改了哪儿、也学不会下次别写。拒收时说清"哪儿有问题",让他删了重发。
   **已知挡不住**:自然语言里的姓名(「我舍友张三也问了」)挡不住,
   那一层只能靠助教自己把关;这里挡的是**结构化**的那两种。
2. **写坏整个文件比不写更糟。** 这份 txt 是几十条手写材料的唯一副本,
   所以落盘用「写临时文件 + `os.replace`」原子替换,并且**写完再读回来核一遍**;
   核不过就按原字节回滚。原则是"要么成功,要么什么都没发生"。
3. **不猜助教的意思。** 缺了答案、一次写两条、答案里带行首的 `Q:`……
   一律拒收并说清原因。宁可让他重发一次,也不要写进一条半截的、
   或者把两条粘成一条的 FAQ:那种条目在检索时**看着像有、其实答错**,
   比没有更坏。
4. **改了必须留痕。** 每次尝试都落一条 `record` 日志(成功与否、拒收理由),
   但不落被拒的正文 —— 既然是因为可能带个人信息才拒的,
   就不该再把那段原文抄进日志。成功的正文落 `q`(它进库后本来就是要给学生看的),
   答案只落长度。

## 已知未做

- `process_input` 那条日志仍然原样记录整条输入(所有消息都这样,不是这个入口特有的),
  所以一条**被拒**的记录,正文还是会出现在那里。要彻底堵得改全项目的输入日志策略,
  不在这次范围内 —— 但写在这里,免得以后有人以为拒收等于"没留下痕迹"。
- 同一秒内两条 `【记录】` 并发会互相覆盖(读-改-写窗口)。一个人用微信发,
  撞不上;真要做多人并发,得加文件锁。
"""
from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass
from pathlib import Path

from src import config
from src.rag.retriever import FAQRetriever
from src.utils.excel_ops import load_roster
from src.utils.logger import log_event
from src.utils.stderr_log import warn as _warn

# 学生学号:8 位以上连续数字。**只挡长的** —— 题号(1.36)、页码(188)、
# 分数(90)都是短数字,挡短数字等于这个入口没法用。
_STUDENT_ID_RE = re.compile(r"\d{8,}")

# 四个标记词都收,是因为助教在手机上打字,少打两个字是常态。
#
# 【勘误 2026-09-20】这里原来写着"「标准答案」必须排在「答案」前面,
# 否则 `标准答案:` 会先匹配上「答案」,留下一个「标准」粘在答案正文开头"。
# **这条推理是错的**:模式是 `^` 锚定的,`^\s*答案\s*[:：]` 在 `标准答案:` 上
# 从第 0 个字符就匹配不上("标准"不是"答案"),根本轮不到 [:：] 那一步 ——
# 分支顺序在锚定模式里不影响结果。是变异测试把它试出来的:
# 把顺序颠倒过来,303 条单测**全绿**。
# 顺序仍然按"长的在前"写着(读起来顺),但它**不承载任何正确性**,
# 别把它当护栏。真正保证答案不被污染的是那个 `^` 和
# `test_record.py::test_标准答案_does_not_leave_a_dangling_标准`。
_Q_LABEL = re.compile(r"^\s*(?:问题|问|Q)\s*[:：]", re.IGNORECASE)
_A_LABEL = re.compile(r"^\s*(?:标准答案|答案|答|A)\s*[:：]", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(?:问题|标准答案|答案|问|答|Q|A)\s*[:：]", re.IGNORECASE)

# 问句限一行话的长度,答案可以是一段。
# 上限不是"技术上做不到更长",而是**超了就该想想**:
# 一条 FAQ 是给"一句话提问"兜底的,超过 200 字的"问题"多半是助教把整段对话粘进来了。
_MAX_Q = 200
_MAX_A = 1000

_USAGE = (
    "用法(直接发给机器人,一条一条来):\n"
    "【记录】问题:学生问的那句话\n"
    "标准答案:你要它以后怎么答\n\n"
    "例:\n"
    "【记录】问题:仿真软件 Proteus 在哪下载?\n"
    "标准答案:课程群文件里有个压缩包,解压后按里面的说明装。"
)


@dataclass
class Parsed:
    """解析结果。`error` 非空 = 拒收,理由直接发给助教。"""

    question: str = ""
    answer: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def strip_prefix(message: str) -> tuple[bool, str]:
    """消息是不是 `【记录】…`。是则返回 (True, 剥掉前缀的正文)。

    和 `main._strip_forward_prefix` 一样容忍前导空格/换行 ——
    助教从微信复制过来时前面常带一个换行。
    """
    stripped = (message or "").lstrip()
    if stripped.startswith(config.RECORD_PREFIX):
        return True, stripped[len(config.RECORD_PREFIX):].strip()
    return False, message


def parse_record(body: str) -> Parsed:
    """把正文解析成 (问题, 答案)。拿不准的一律返回 `error`,不猜。"""
    lines = (body or "").splitlines()

    qi = next((i for i, l in enumerate(lines) if _Q_LABEL.match(l)), -1)
    if qi < 0:
        return Parsed(error="没找到「问题:」这一行。\n\n" + _USAGE)

    ai = next((j for j in range(qi + 1, len(lines)) if _A_LABEL.match(lines[j])), -1)
    if ai < 0:
        return Parsed(error="只看到了「问题:」,没看到「标准答案:」。\n\n" + _USAGE)

    # 正文里不许再出现行首标记 —— 两种后果不一样,所以分开报。
    # 只看"除标记行以外"的部分:答案那一行本身(第 ai 行)要跳过,
    # 否则它自己就会被判成"又有标记"。
    for k in range(qi + 1, len(lines)):
        if k == ai:
            continue
        if _Q_LABEL.match(lines[k]):
            # 又出现「问题:」= 这是两条,不是一条。
            return Parsed(error=(
                f"一次只能记录一条:第 {k + 1} 行又出现了「问题:」这类开头。\n"
                "两条粘在一起没有唯一解 —— 猜错就是一条内容错位的 FAQ 被安静地写进去,\n"
                "学生问 A 拿到 B 的答案,谁都不会发现。要记两条请分两次发。"
            ))
        if _A_LABEL.match(lines[k]):
            # 又出现「答案:」/「A:」= 要么两条,要么答案正文里带标记。
            # `A:` 这种**英文**写法尤其危险:`_parse_faq` 就是靠行首的 Q:/A: 划分条目的,
            # 它会把这一条从那儿截断,后半段变成"开局就没有答案的条目"被整段丢掉。
            return Parsed(error=(
                f"第 {k + 1} 行又是一个「答案:」/「A:」这类开头。\n"
                "FAQ 文件靠行首的 Q:/A: 划分条目,留在正文里会把这一条截成两半;\n"
                "如果是想记两条,请分两次发。改一下那一行的写法(比如缩进一点)也可以。"
            ))

    question = _join([_Q_LABEL.sub("", lines[qi])] + lines[qi + 1:ai])
    answer = _join([_A_LABEL.sub("", lines[ai])] + lines[ai + 1:])

    if not question:
        return Parsed(error="「问题:」后面是空的。")
    if not answer:
        return Parsed(error="「标准答案:」后面是空的。")
    return Parsed(question=question, answer=answer)


def _join(parts: list[str]) -> str:
    """拼多行文本:去掉空行、每行 strip,再用换行接起来。

    去掉空行是有理由的:`_parse_faq` 用**空行 + 下一条 Q** 判断条目结束,
    正文里夹一个空行虽然当下不会解析错,但下一个条目万一被人手工插进来,
    空行就变成了歧义的来源 —— 让写进去的东西尽量没有多余结构。
    """
    return "\n".join(p.strip() for p in parts if p.strip())


def _pii_reason(text: str, names: set[str]) -> tuple[str, str]:
    """文本里有没有学生的结构化个人信息?

    返回 `(类型, 理由)`,类型用来写日志和回执的抬头;理由非空 = 有。
    **理由里不回显任何个人信息本身** —— 回显等于又抄了一遍,
    而回执是要发到微信里去的。
    """
    m = _STUDENT_ID_RE.search(text)
    if m:
        return "学号", (
            f"这段里有像学号的连续数字「{m.group(0)[:2]}……」(共 {len(m.group(0))} 位)。\n"
            "FAQ 是要发给学生的,不能写进任何个人信息。请把它删掉再发一次。"
        )
    hit = sorted(n for n in names if n and n in text)
    if hit:
        # 只报"有几处、几个字",不回显姓名本身。
        return "学生姓名", (
            f"这段里出现了花名册上的学生姓名(共 {len(hit)} 处,{len(hit[0])} 字)。\n"
            "FAQ 是要发给学生的,不能写进任何个人信息。请改用「某同学」这类说法再发一次。"
        )
    return "", ""


def _too_long_reason(question: str, answer: str) -> str:
    if len(question) > _MAX_Q:
        return (f"问题太长了({len(question)} 字,上限 {_MAX_Q})。"
                "一条 FAQ 是对「一句话提问」兜底的,太长多半是整段对话粘进来了。")
    if len(answer) > _MAX_A:
        return (f"标准答案太长了({len(answer)} 字,上限 {_MAX_A})。"
                "太长的答复塞进 FAQ 会挤掉课程资料,建议拆成几条、或写成事务事实。")
    return ""


_PUNCT_RE = re.compile(r"[\s,，。.、!！?？;；:：'\"“”‘’()（）\[\]【】{}<>《》~～\-—_*#/\\|]+")


def _normalize(text: str) -> str:
    """比"两条问法是不是同一条"时用的归一化:去掉空白与标点、统一小写。

    数字**留着**(「第2次」和「第3次」是两条不同的 FAQ)。
    """
    return _PUNCT_RE.sub("", text).lower()


def _atomic_append(path: Path, question: str, answer: str) -> None:
    """把一条 Q/A 追加到文件末尾,**原子替换**,并保留原有的换行风格与 BOM。

    保留换行风格不是洁癖:这份 txt 是用 Windows 记事本编辑的,
    混进 LF 之后整份文件的换行会变得一半一半,`git diff` 和文本比较工具都会失真。
    """
    raw = path.read_bytes()
    had_bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    nl = "\r\n" if "\r\n" in text else "\n"

    if text and not text.endswith(("\n", "\r")):
        text += nl
    if text.strip():
        text += nl          # 条目之间空一行
    text += f"Q:{nl}{question}{nl}A:{nl}{answer}{nl}"

    data = text.encode("utf-8")
    if had_bom:
        data = codecs.BOM_UTF8 + data

    # 临时文件名带 pid:万一有两条同时落盘,至少不会互相写进同一个临时文件。
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def handle(msg: str,
           faq_path: Path | None = None,
           roster: dict[str, str] | None = None) -> dict:
    """处理一条 `【记录】…`。`msg` 带不带前缀都行(带了会自己剥掉)。

    返回的 `reply` 是**直接发回给助教**的纯文本(和答疑回复同一个契约:
    不带 Markdown,微信不渲染)。
    """
    path = faq_path or config.FAQ_PATH
    _matched, body = strip_prefix(msg)
    if not body:
        return _refuse(path, "空指令", "要记录什么?后面还得写上问题和答案。\n\n" + _USAGE)

    parsed = parse_record(body)
    if not parsed.ok:
        return _refuse(path, "格式不对", parsed.error)

    if roster is None:
        try:
            roster = load_roster()
        except Exception as exc:  # noqa: BLE001 —— 花名册读不出来不该拦住记录
            _warn(f"[记录] 花名册读不了({exc}),这次只查学号、不查姓名")
            roster = {}
    names = set(roster.values())

    for tag, text in (("问题", parsed.question), ("标准答案", parsed.answer)):
        kind, reason = _pii_reason(text, names)
        if reason:
            return _refuse(path, f"{tag}里有{kind}", f"{tag}:{reason}")
    reason = _too_long_reason(parsed.question, parsed.answer)
    if reason:
        return _refuse(path, "太长", reason)

    if not path.exists():
        # 刻意**不自动创建**:路径配错时自动建文件会造成"两边各有一份 FAQ",
        # 助教以为记进去了,学生那边一条都搜不到 —— 静默分裂,最难发现。
        return _refuse(path, "FAQ 文件不存在",
                       f"没找到 {path}\n"
                       "为避免在错的地方新建一份 FAQ(那样记进去也不会被检索到),这里不自动创建。\n"
                       "请先确认这个路径对不对。")

    raw_before = path.read_bytes()
    existing = FAQRetriever.from_file(path)
    for i, (q, _a) in enumerate(existing.qa_pairs, 1):
        if _normalize(q) == _normalize(parsed.question):
            return _refuse(path, "重复", f"这条问法库里已经有了(第 {i} 条):\n{q}")

    try:
        _atomic_append(path, parsed.question, parsed.answer)
    except OSError as exc:
        return _refuse(path, "写盘失败", f"写不进 {path}:{exc}")

    after = FAQRetriever.from_file(path)
    if not any(q == parsed.question and a == parsed.answer for q, a in after.qa_pairs):
        # 写进去了却读不回来 —— 说明这份文件现在的结构已经和解析器对不上了。
        # 按原字节回滚,保持"要么成功、要么什么都没发生"。
        path.write_bytes(raw_before)
        _warn(f"[记录] 写入后回读不到,已回滚:{path}")
        return _refuse(path, "写入后读不回来",
                       "已把你这句话写进文件,但再读的时候解析不出来 —— "
                       "说明这份 FAQ 文件的结构有问题(可能有一条手工写的条目格式不对)。\n"
                       "**已按原样回滚,文件没有变化**,请先检查文件再记。")

    n = len(after.qa_pairs)
    added = n - len(existing.qa_pairs)

    lines = [f"✅ 已记录,FAQ 现在能被检索到 {n} 条。", "",
             f"问:{parsed.question}",
             f"答:{parsed.answer if len(parsed.answer) <= 120 else parsed.answer[:120] + '……'}"]

    # 顺手提醒"库里已有一条很像的":不是拒收理由(学生可能真的两种说法都问),
    # 但重复条目会互相抢分,助教看一眼更放心。
    near = [h for h in existing.search(parsed.question, top_k=1)
            if h["score"] >= config.FAQ_HIT_THRESHOLD]
    if near:
        lines += ["", f"提醒:库里已有一条很像的(第 {_index_of(existing, near[0]['question'])} 条):"
                      f"{near[0]['question']}\n如果那是同一条,建议你手工把它删掉;不是的话忽略这句。"]

    if not names:
        lines += ["", "⚠ 花名册没读到,这次只查了学号、没查姓名 —— 记之前请自己确认没有学生姓名。"]

    lines += ["", f"文件:{path}"]
    reply = "\n".join(lines)
    log_event("record", ok=True, path=str(path), n_entries=n, added=added,
              q_len=len(parsed.question), a_len=len(parsed.answer),
              q=parsed.question[:200], near_dup=bool(near), roster_size=len(names))
    return {"type": "record", "reply": reply}


def _index_of(retriever: FAQRetriever, question: str) -> int:
    """问法在库里的第几条(1-based)。找不到返回 0(调用方只在命中后用)。"""
    for i, (q, _a) in enumerate(retriever.qa_pairs, 1):
        if q == question:
            return i
    return 0


def _refuse(path: Path, reason_tag: str, reply: str) -> dict:
    """统一的拒收回执:发回给助教的那句 + 日志。

    **日志里不落正文**,只落理由和长度 —— 既然是因为"可能带个人信息"才拒的,
    就不该再把那段原文抄进日志一份。成功那条反而落了 `q`,因为进库之后
    它本来就是要给学生看的。
    """
    log_event("record", ok=False, path=str(path), reason=reason_tag, reply_len=len(reply))
    return {"type": "record", "reply": f"❌ 没记: {reason_tag}\n\n{reply}"}
