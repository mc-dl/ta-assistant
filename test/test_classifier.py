"""分类器单元测试(不依赖 LLM,只测规则层)。"""
from __future__ import annotations

from src.handlers.classifier import classify


def test_submission_with_id_and_keyword():
    msg = "老师您好 数电作业昨天没交上去 张三 20230001"
    assert classify(msg) == "submission"


def test_submission_various_keywords():
    for kw in ["漏交", "补交", "没交", "忘了交"]:
        msg = f"学号 20230001 {kw} 作业"
        assert classify(msg) == "submission", f"failed on {kw}"


def test_question_with_mark():
    assert classify("Proteus 在哪下载?") == "question"
    assert classify("第一章作业怎么批改?") == "question"


def test_question_with_keywords():
    assert classify("学长好,能不能发一下 PPT") == "question"
    assert classify("请问作业什么时候改完") == "question"


def test_empty_and_unknown():
    assert classify("") == "unknown"
    # 无法判断的消息(兜底为 question)
    assert classify("好的,谢谢") == "question"
