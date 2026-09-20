#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给「电路基础」作业答案生成**中文详细解析**(调大模型),写回知识库语料。

为什么要这个脚本(2026-09-20 实测):
    原始答案是 Alexander & Sadiku 第 6 版习题解答,**英文且极度简略** ——
    1.36 题全文就两行:
        (a) I = 20/0.25 = 80 amps.
        (b) days = (20/0.002)/24 = 416.7 days.
    学生看不懂 0.25 是哪来的(15 分钟 = 0.25 小时)。**检索得到,但答不了疑。**
    中文参考答案 docx 同样简略,还带输入笔误(如把 v2·9/8 打成 v29/8)。

    所以逐题调大模型,把原解答**展开**成中文详细解析,写回同一个语料文件:
    中文解析在前(检索和阅读都用它),原文附在后(供核对)。

三类源(2026-09-20 晚新增第三类):
    cn-v1  中文版作业用书(`202509电路理论基础/Z第N章*.pdf`)—— **权威版**。
           助教明确:国际版个别题参数/单位不同(公里↔英里),**课后题答案以中文版为准**。
           有中文版的题,英文原文降级成文末的「英文版对照(仅参考)」。
           提示词不是"翻译"而是"把简略的中文解答展开",并额外告诫大模型:
           PDF 抽取会把上下标甩到下一行(`R` 换行 `1 2 3` 其实是 R₁R₂R₃)、
           公式符号可能丢 —— 这两种失真**只在解释里还原,不许动数值**。
    pdf    英文版 SolnNNNNN.pdf —— 只保留中文版作业**没布置**的题(如第 13、19 章)
    docx   张老师的中文参考答案 docx

三条纪律(重要,别破坏):
    1. **不许改数值**:提示词要求原样保留所有数字/单位/结论,原文没写的不许补。
    2. **原文一字不动地附在文件末尾**:助教可以逐行核对大模型有没有编。
       这也是为什么本脚本**不删原文** —— 大模型出错时,原文是唯一的事实来源。
    3. **提示词版本化**:改了提示词就 bump PROMPT_VERSION(或换 kind,如 cn-v1→cn-v2),
       缓存自动失效重跑。

    ⚠️ 文件路径和文件格式由 `import_circuit_theory.Spec/render` 统一提供,
    本脚本**不自己拼文件内容** —— 否则重写时容易漏掉英文对照块。

用法(WSL 内):
    python scripts/enhance_circuit_basic.py --dry-run          # 只看要花多少次调用
    python scripts/enhance_circuit_basic.py --workers 5        # 实际跑(可断点续跑)
    python scripts/enhance_circuit_basic.py --only 1.36        # 只重做某一题
    python scripts/enhance_circuit_basic.py --no-theory        # 不碰中文版作业用书
    python scripts/enhance_circuit_basic.py --no-cache         # 全部重做

完整链路:import_circuit_theory.py → 本脚本 → build_index.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_circuit_basic as base  # noqa: E402
from src import config  # noqa: E402
from src.llm.minmax import MinMaxClient  # noqa: E402
from src.utils.stderr_log import warn as _warn  # noqa: E402

# 改了提示词/输出格式就 bump 这个,缓存会整体失效重跑
PROMPT_VERSION = "v1"

# 补进标题头「检索:」行的关键词 —— 学生实际就是这么问的("1.36 怎么做")。
# base._header 的 extra 参数会把这串塞进检索行,jieba 按空格切开逐个成词。
_EXTRA_KW = "详细解析 讲解 详解 思路 步骤 方法 怎么做 怎么求 怎么算 中文"

_SYSTEM = (
    "你是中山大学《电路基础》课程的助教,任课老师是张曰理。"
    "你的任务是把课后习题的标准答案整理成**中文的详细解析**,讲给大一学生听。"
    "你只做翻译、展开和解释,不改变任何数值与结论。"
)

# 共用的写作要求。{extra} 放"针对这一份原文"的特殊要求。
_COMMON_RULES = """1. 分五节,每节用【】做小标题:【题目要求】【已知条件】【解题思路】【详细步骤】【最终答案】
2. 公式写成普通文本,例如:I = 20 A、v1 = 15 V、Z = (3 + j4) Ω。
   不要用 LaTeX($...$),也不要用 Markdown 符号(**、# 开头、- 开头)。
3. **数值、单位、结论必须与原文完全一致,一个数字都不许改**。
   原文没给出的推导不要自己补 —— 宁可写"原文此处省略了中间推导"。
4. 原文跳过的**基础换算**可以补一句说明,但必须是恒等变形,且要写出算式。
   例如原文写 I = 20/0.25,可以补充"15 分钟 = 0.25 小时"。
5. 若原文提到"如图/下图/figure"而答案里没有图,就在该处注明
   "(此处依赖题图,请对照作业册或原始 PDF)"。**不要凭空描述电路长什么样。**
6. 术语用中文,专有名词首次出现时在括号里附英文,如"相量(phasor)"。
7. 篇幅 350~800 字,不要注水。
8. 如果原文太少、展开不成完整解析,就照实写简短些,不要硬凑。
9. 直接输出解析正文,不要写"好的""以下是"这类开场白。
{extra}"""

_USER_PDF = f"""下面是一道《电路基础》课后作业题的标准答案原文(**英文**,来自 Alexander & Sadiku \
《Fundamentals of Electric Circuits》第 6 版的习题解答)。

请把它整理成一份中文详细解析。

{_COMMON_RULES.format(extra='')}
原文:
---
{{body}}
---
"""

_USER_DOCX = f"""下面是一道《电路基础》课后作业题的参考答案原文。原文是**中文**,由任课老师提供,\
但**很简略**,并且**可能有输入笔误**(例如把 v2·9/8 打成 v29/8)。

请把它整理成一份中文详细解析。

{_COMMON_RULES.format(extra="""10. 原文如果某个式子明显不完整或有笔误(例如 "RN = V0 / I0 =" 后面就没有了),
    请**原样引用**,并在括号里注明"(原文此处不完整)",**不要替它补全或改写**。
11. 原文如果只有算式、没有文字说明,请说明**每个算式在求什么**,
    但不要编造原文没有的公式来源。
""")}
原文:
---
{{body}}
---
"""

# 中文版作业用书(**权威版**)的原文 —— 与 _USER_DOCX 不同:这份是任课老师指定的
# 中文版配套作业答案,是**数值与结论的唯一权威来源**(国际版个别题参数不同,如公里↔英里)。
# 所以这里的任务不是"翻译",而是**把简略的中文解答展开成学生看得懂的过程**。
_USER_CN = f"""下面是一道《电路基础》课后作业题参考答案的原文。原文是**中文**,来自\
**任课老师指定的中文版配套作业用书**,是**本课程数值与结论的权威版本**。

原文很简略(常常只有几行算式),学生的困难是"看不懂这个式子哪来的"。

请把它整理成一份中文详细解析,**在原文基础上展开讲清楚**,不要另起炉灶。

{_COMMON_RULES.format(extra="""10. **这是中文原文,不要翻译成英文**,也不要把中文术语换成英文。
11. 原文的数值、单位、答案**一个字都不许改**(包括小数位数与正负号)。
    你补充的只能是"这个式子是怎么来的"这类**解释性**内容。
12. PDF 抽取的两个已知失真,请据上下文还原(但**不要改变数值本身**):
    (a) **上下标会掉到下一行**,例如正文写 "R 、R 、R" 而下一行是 "1 2 3",
        应理解为 R₁、R₂、R₃(读作 R1、R2、R3);相量的角标同理;
    (b) 公式里的运算符号有时会丢(如乘法符号、分数线),
        若能从上下式推出原意就补一句说明,推不出就在该处注明
        "(原文公式抽取残缺,请对照作业册)"。
13. 原文偶有**输入笔误**(如把 v2·9/8 打成 v29/8)。请**原样引用**原文,
    再在括号里注明"(原文此处疑为笔误,按 v2·9/8 理解)" —— 不要默默改掉。
14. 遇到原文只给"解:xxx"而没有题干的,不要自己臆造题干,
    直接按公式能确定的内容讲解,并注明"(题干请对照作业册)"。
""")}
原文:
---
{{body}}
---
"""


@dataclass
class Job:
    """一道题的增强任务。

    注意 kind 直接决定缓存命名空间。中文版作业答案用 "cn-v1":它是**新加的源**,
    所以天然拿不到旧缓存、必然重新生成 —— 这正是我们要的(旧的详细解析是按英文版
    写的,数字可能是错的那一版)。反过来,只有英文版的题(第13、19章等)kind 仍是
    "pdf"、body 没变,**缓存命中、不重复花钱**,它们的详细解析继续有效。
    改中文版的提示词时,把 "cn-v1" 改成 "cn-v2" 即可让这部分精确失效。
    """
    kind: str            # "pdf"(英文单题解答)/ "docx"(中文参考答案)/ "cn-v1"(中文版作业用书)
    label: str           # 题号/小节名,只用于日志
    header: str          # 中文标题头(检索锚点)
    body: str            # 原文
    out: Path            # 写到哪里
    src_name: str
    spec: object = None  # import_circuit_theory.Spec(中文版题才有),重写文件时用
    key: str = field(default="", init=False)

    def __post_init__(self) -> None:
        raw = f"{PROMPT_VERSION}\x00{self.kind}\x00{self.body}"
        self.key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _collect_jobs(src: Path, out: Path, theory_src: Path | None = None) -> list[Job]:
    """扫源目录,产出增强任务。抽取逻辑全部复用 import_* 脚本,不重复实现。

    三类源,优先级从高到低:
      ③ 中文版作业用书(theory_src)→ kind="cn-v1",**权威版**
      ① 英文版单题解答 SolnNNNNN.pdf → kind="pdf"(被 ③ 覆盖的题**跳过**)
      ② 中文参考答案 docx         → kind="docx"
    """
    jobs: list[Job] = []

    # ③ 中文版作业用书 —— 先收,好把英文版里同题号的挤掉
    cn_probs: set[str] = set()
    if theory_src and theory_src.exists():
        import import_circuit_theory as theory
        for spec in theory.theory_specs(theory_src, out):
            prob = spec.dest.stem
            cn_probs.add(prob)
            jobs.append(Job(
                kind="cn-v1", label=prob, src_name=spec.dest.parent.name,
                body=spec.body, header=spec.header, out=spec.dest, spec=spec,
            ))

    # ① 每章的单题解答 SolnNNNNN.pdf
    for sub in sorted(p for p in src.iterdir() if p.is_dir()):
        m = base._CHAPTER_DIR.match(sub.name)
        if not m:
            continue
        ch_from_dir, chapter_name = str(int(m.group(1))), m.group(2)
        for pdf in sorted(sub.glob("*.pdf")):
            body = base._pdf_text(pdf)
            if len(body) < 60:
                continue  # 扫描件之类,base 那边已记过原因
            body = base.strip_copyright(body)
            ch, prob, _how = base._problem_ref(pdf, body)
            if not ch:
                ch = ch_from_dir
            if prob in cn_probs:
                # 这题中文版作业用书里有 —— 英文版**不再单独成篇**,它的原文会被
                # 当成「英文版对照」附在中文版那篇的末尾(见 import_circuit_theory)。
                # 若还给它建 job,两个 job 会写同一个文件,谁后写谁赢。
                continue
            jobs.append(Job(
                kind="pdf", label=prob, src_name=pdf.name, body=body,
                header=base._header(ch, prob, chapter_name, pdf.name, extra=_EXTRA_KW),
                out=out / sub.name / f"{prob}.md",
            ))

    # ② 中文参考答案 docx(按小节拆)
    for docx in sorted(src.glob("*.docx")):
        if docx.name.startswith("~$"):
            continue
        text = base._docx_text(docx)
        if len(text) < 60:
            continue
        for i, (label, body) in enumerate(base._split_docx_sections(text), 1):
            header = (
                f"【{base.COURSE} 作业参考答案(中文) {label}】任课老师:{base.TEACHER}\n"
                f"来源:{docx.name}\n"
                f"检索:{base.COURSE} 作业 答案 解答 参考 中文 {label} 习题 {_EXTRA_KW}\n"
            )
            jobs.append(Job(
                kind="docx", label=label, src_name=docx.name, body=body,
                header=header,
                out=out / "参考答案" / f"{docx.stem}_{i:02d}_{label}.md",
            ))
    return jobs


def _render(job: Job, chinese: str) -> str:
    """拼出最终语料。chinese 为空表示这题增强失败,退化为原始语料(不写空章节)。

    中文版题(带 spec)交给 import_circuit_theory.render 拼 —— 因为要带文末的
    「英文版对照」块,而那个块的格式只能有一个来源,不能两边各写一份。
    """
    if job.spec is not None:
        import import_circuit_theory as theory
        return theory.render(job.spec, chinese)
    if not chinese.strip():
        return job.header + "\n" + job.body + "\n"
    return (
        job.header
        + "\n【中文详细解析】\n" + chinese.strip() + "\n"
        + "\n【原文(核对用,未经改动)】\n" + job.body + "\n"
    )


# 每个线程一个客户端:MinMaxClient 本身是无状态的 requests 调用,
# 但 x-opencode-session 是**会话级**的,给每个线程独立 session 更稳妥。
_tls = threading.local()


def _client() -> MinMaxClient:
    c = getattr(_tls, "client", None)
    if c is None:
        c = MinMaxClient.from_env()
        c.timeout = max(c.timeout, 120)   # 生成 800 字中文,20s 的默认值不够
        c.max_retries = max(c.max_retries, 4)
        _tls.client = c
    return c


def _enhance(job: Job) -> str:
    tpl = {"pdf": _USER_PDF, "docx": _USER_DOCX}.get(job.kind, _USER_CN)
    return _client().chat(_SYSTEM, tpl.format(body=job.body), temperature=0.2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=base.DEFAULT_SRC)
    ap.add_argument("--theory-src", type=Path, default=None,
                    help="中文版作业用书/课件的目录(默认自动找 202509电路理论基础)")
    ap.add_argument("--no-theory", action="store_true", help="不处理中文版作业用书")
    ap.add_argument("--out", type=Path, default=None,
                    help="默认落到 MATERIALS_DIR/电路基础/(与 import_circuit_basic 相同)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default=None, help="只处理某个题号/小节,如 1.36")
    ap.add_argument("--no-cache", action="store_true", help="忽略缓存,全部重做")
    ap.add_argument("--dry-run", action="store_true", help="只统计,不调 API、不写文件")
    args = ap.parse_args()

    src: Path = args.src
    out: Path = args.out or (config.MATERIALS_DIR / base.COURSE)
    if not src.exists():
        _warn(f"[enhance] 源目录不存在:{src}")
        return 1

    cache_path = out / "_enhance_cache.json"
    cache: dict[str, dict] = {}
    if cache_path.exists() and not args.no_cache:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")).get("entries", {})
        except Exception as e:                                    # noqa: BLE001
            _warn(f"[enhance] 缓存读取失败,按空缓存处理:{e}")

    # 中文版作业用书:默认自动找,找不到就只做英文 Soln / 中文 docx 那两类源
    import import_circuit_theory as theory
    theory_src: Path | None = None
    if not args.no_theory:
        cand = args.theory_src or theory.DEFAULT_SRC
        if cand.exists():
            theory_src = cand
        else:
            _warn(f"[enhance] ⚠️ 找不到中文版作业用书目录({cand}),"
                  f"本次只处理英文 Soln 与中文 docx 两类源")

    jobs = _collect_jobs(src, out, theory_src)
    if args.only:
        jobs = [j for j in jobs if j.label == args.only]
    if not jobs:
        _warn("[enhance] 没有可处理的题(检查 --src / --only)")
        return 1

    kinds = Counter(j.kind for j in jobs)
    _warn(f"[enhance] 任务构成:" + "、".join(f"{k} {v} 题" for k, v in sorted(kinds.items())))

    pending = [j for j in jobs
               if args.no_cache or j.key not in cache or not cache[j.key].get("text")]

    _warn(f"[enhance] 源:{src}")
    _warn(f"[enhance] 输出:{out}")
    _warn(f"[enhance] 共 {len(jobs)} 题,其中已缓存 {len(jobs) - len(pending)} 题,"
          f"本次需调 {len(pending)} 次大模型")

    if args.dry_run:
        _warn("[enhance] --dry-run 结束(未调用 API、未写文件)")
        for j in pending[:10]:
            _warn(f"    - 待增强 {j.label:16s} {j.src_name}")
        if len(pending) > 10:
            _warn(f"    - ... 另有 {len(pending) - 10} 题")
        return 0

    # ── 调大模型 ──────────────────────────────────────────────
    failed: list[Job] = []
    done = 0
    t0 = time.time()
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {pool.submit(_enhance, j): j for j in pending}
            for fut in as_completed(futs):
                job = futs[fut]
                try:
                    text = fut.result()
                except Exception as e:                            # noqa: BLE001
                    _warn(f"[enhance] ✗ {job.label} 异常:{e}")
                    text = ""
                if text.strip():
                    cache[job.key] = {
                        "label": job.label, "src": job.src_name,
                        "model": _client().model, "prompt_version": PROMPT_VERSION,
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                        "text": text.strip(),
                    }
                else:
                    failed.append(job)
                done += 1
                if done % 10 == 0 or done == len(pending):
                    _warn(f"[enhance] 进度 {done}/{len(pending)}"
                          f"  失败 {len(failed)}  已用 {time.time() - t0:.0f}s")
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    _write_cache(cache_path, cache, src, out)

    # ── 写语料(失败的题也写,只是没有中文解析,保证知识库不残缺) ──
    written = 0
    for job in jobs:
        entry = cache.get(job.key) or {}
        job.out.parent.mkdir(parents=True, exist_ok=True)
        job.out.write_text(_render(job, entry.get("text", "")), encoding="utf-8")
        written += 1
    _write_cache(cache_path, cache, src, out)

    ok = sum(1 for j in jobs if (cache.get(j.key) or {}).get("text"))
    _warn(f"[enhance] 写出 {written} 个语料文件,其中带中文详细解析的 {ok} 题")
    if failed:
        _warn(f"[enhance] ⚠️ {len(failed)} 题增强失败(已写入未增强版,"
              f"**重跑本脚本即可补上**):")
        for j in failed:
            _warn(f"    - {j.label}  ({j.src_name})")
    _warn("[enhance] 别忘了重建索引:python scripts/build_index.py")
    return 0


def _write_cache(path: Path, cache: dict, src: Path, out: Path) -> None:
    path.write_text(json.dumps({
        "prompt_version": PROMPT_VERSION,
        "source_dir": str(src),
        "output_dir": str(out),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": cache,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
