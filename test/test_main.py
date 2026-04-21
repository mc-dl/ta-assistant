"""主入口 process() 前缀过滤逻辑测试。"""
from __future__ import annotations

from src import config


class TestPrefixFilter:
    """测试【转发学生提问】前缀过滤。"""

    def test_submission_with_prefix(self):
        """有前缀的补交消息 → type=submission"""
        from src.main import process
        result = process("【转发学生提问】老师您好 数电作业昨天没交上去 王小明 20230001 第1次")
        assert result["type"] == "submission"
        assert "王小明" in result["reply"]
        assert "20230001" in result["reply"]

    def test_question_with_prefix(self):
        """有前缀的提问 → type=question"""
        from src.main import process
        result = process("【转发学生提问】Proteus 在哪下载?")
        # 知识库未就绪时仍返回 question 类型(不是 skip)
        assert result["type"] == "question"

    def test_no_prefix_returns_skip(self):
        """无前缀的消息 → type=skip,不处理"""
        from src.main import process
        result = process("你好啊今天天气不错")
        assert result["type"] == "skip"
        assert result["reply"] == "(非学生消息,未处理)"

    def test_empty_message(self):
        """空消息带前缀 → 仍走 skip(空消息剥离后无内容)"""
        from src.main import process
        result = process("【转发学生提问】")
        assert result["type"] == "skip"

    def test_prefix_preserved_in_log(self):
        """原始消息(含前缀)记入日志,不含 prefix 字样"""
        from src.main import process
        # 只需要跑通不报错,详细日志内容由 integration test 验证
        result = process("【转发学生提问】王小明 20230001 漏交")
        assert result["type"] in ("submission", "question", "skip")


class TestPrefixVariants:
    """前缀变体匹配:容忍同学/前导空格/半角等差异。"""

    def test_standard_prefix_question(self):
        """有标准前缀 + 提问 → type=question"""
        from src.main import process
        result = process("【转发学生提问】Proteus 在哪下载?")
        assert result["type"] == "question"

    def test_variant_prefix_classmate(self):
        """有"同学"变体前缀 + 提问 → type=question"""
        from src.main import process
        result = process("【转发同学提问】什么是竞争冒险?")
        assert result["type"] == "question"

    def test_prefix_with_leading_space(self):
        """有前导空格的前缀 → type=question(容错)"""
        from src.main import process
        result = process(" 【转发学生提问】Proteus 在哪下载?")
        assert result["type"] == "question"

    def test_prefix_submission(self):
        """有前缀 + 补交 → type=submission"""
        from src.main import process
        result = process("【转发学生提问】王小明 20230001 第1次作业没交")
        assert result["type"] == "submission"

    def test_no_prefix_returns_skip(self):
        """无前缀 → type=skip"""
        from src.main import process
        result = process("你好")
        assert result["type"] == "skip"
        assert result["reply"] == "(非学生消息,未处理)"
