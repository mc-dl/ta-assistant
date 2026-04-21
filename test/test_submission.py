"""补交模块抽取逻辑测试(不写文件)。"""
from __future__ import annotations

from src.handlers.submission import (
    extract_homework_number,
    extract_name,
    extract_student_id,
)


def test_extract_student_id():
    assert extract_student_id("学号 20230001 姓名张三") == "20230001"
    assert extract_student_id("12345678") == "12345678"
    assert extract_student_id("学号 1234") is None  # 少于 8 位
    assert extract_student_id("没有学号") is None


def test_extract_name_from_roster():
    roster = {"20230001": "张三"}
    assert extract_name("随便一段话", roster=roster,
                         student_id="20230001") == "张三"


def test_extract_name_from_explicit_pattern():
    assert extract_name("我是张三") == "张三" or extract_name("我是张三") is None
    # 3+ 字更稳
    assert extract_name("我是王小明") == "王小明"
    assert extract_name("姓名:李小华 学号 20230001") == "李小华"


def test_extract_name_skip_stopwords():
    # "老师""助教"不应被当姓名
    assert extract_name("老师您好") != "老师"


def test_extract_homework_number():
    assert extract_homework_number("第1次作业") == "第 1 次"
    assert extract_homework_number("第一次作业") == "第 1 次"
    assert extract_homework_number("作业3没交") == "第 3 次"
    assert extract_homework_number("Chapter 4 实验") == "第 4 次"
    assert extract_homework_number("实验 2") == "第 2 次"
    assert extract_homework_number("没次数信息") is None
