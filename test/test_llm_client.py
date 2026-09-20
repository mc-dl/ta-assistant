"""大模型客户端的传输层:`chat()` 到底发了什么请求、拿到各种响应怎么办。

这些测试之所以能**离线**跑,是因为 conftest 的 `offline_http` 已经把
`requests.post` 换成了假的(见那里的注释)。本文件再按需要替换成"按脚本返回"
的版本,于是"4xx 不重试""429 才重试""缺 key 不发请求"这些行为都能验。

为什么专门测这个:`src/llm/minmax.py` 里踩过两个只有实测才暴露的坑 ——
Cloudflare 拦 python-requests 默认 UA(403 error code:1010)、
OpenCode Go 缺 session 头直接 400 MissingSessionID。这两条都是**静默**的:
少一个头,表现是"模型不回答",而不是报错。所以值得钉住。
"""
from __future__ import annotations

import types

import pytest
import requests

from src.llm import minmax as minmax_mod
from src.llm.minmax import MinMaxClient, _extract_text

ENDPOINT = "https://example.invalid/v1/chat/completions"


class Resp:
    """够用的假响应:chat() 只看 status_code / text / json()。"""

    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def _ok(content: str, key: str = "content") -> Resp:
    return Resp(200, {"choices": [{"message": {key: content}}]})


def make_client(**kw) -> MinMaxClient:
    base = dict(api_key="k", group_id="", model="m", endpoint=ENDPOINT)
    base.update(kw)
    return MinMaxClient(**base)


@pytest.fixture
def scripted_post(monkeypatch):
    """把 requests.post 换成"按脚本逐次返回"的假实现,返回请求记录列表。"""
    def install(responses: list):
        seen: list[dict] = []
        queue = list(responses)

        def _post(url, json=None, headers=None, timeout=None, **kw):  # noqa: A002
            seen.append({"url": url, "json": json, "headers": headers,
                         "timeout": timeout})
            if not queue:
                raise AssertionError(
                    f"requests.post 被调了 {len(seen)} 次,超出脚本给的 "
                    f"{len(responses)} 次 —— 多出来的那次要么是重试逻辑错了,"
                    f"要么是本来不该发请求"
                )
            spec = queue.pop(0)
            if isinstance(spec, Exception):
                raise spec
            return spec

        monkeypatch.setattr(requests, "post", _post, raising=True)
        return seen
    return install


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """重试的指数退避别真的睡:3 次重试要等 0.5+1.0=1.5 秒,纯浪费。"""
    monkeypatch.setattr(
        minmax_mod, "time",
        types.SimpleNamespace(sleep=lambda s: None, time=lambda: 0.0),
        raising=True,
    )


# ─── 请求该长什么样 ───────────────────────────────────────────
def test_chat_sends_session_header_and_user_agent(scripted_post):
    """OpenCode Go 两个必需项:session 头 + 伪装 UA。少一个就是静默不回答。"""
    seen = scripted_post([_ok("答")])
    c = make_client(session_header="x-opencode-session", session_id="sid-1",
                    user_agent="Mozilla/5.0 (fake)")
    c.chat("系统", "用户")

    h = seen[0]["headers"]
    assert h["x-opencode-session"] == "sid-1"
    assert h["User-Agent"] == "Mozilla/5.0 (fake)"
    assert h["Authorization"] == "Bearer k"
    assert seen[0]["url"] == ENDPOINT


def test_group_id_only_sent_when_set(scripted_post):
    """MiniMax 兜底通道要 group_id,Go 通道不要 —— 空的时候不能发这个字段。"""
    seen = scripted_post([_ok("答"), _ok("答")])
    make_client(group_id="").chat("s", "u")
    make_client(group_id="g1").chat("s", "u")
    assert "group_id" not in seen[0]["json"]
    assert seen[1]["json"]["group_id"] == "g1"


def test_prompt_carries_system_and_user(scripted_post):
    seen = scripted_post([_ok("答")])
    make_client().chat("系统提示", "学生问题", temperature=0.7)
    msgs = seen[0]["json"]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == "系统提示"
    assert msgs[1]["content"] == "学生问题"
    assert seen[0]["json"]["temperature"] == 0.7


def test_missing_key_makes_no_request(scripted_post):
    """没配 key 时**一个请求都不该发**,直接返回空串(兜底通道为空的老情况)。"""
    seen = scripted_post([])          # 脚本为空:发了请求就会 AssertionError
    c = MinMaxClient(api_key="", group_id="", model="m", endpoint=ENDPOINT)
    assert c.available() is False
    assert c.chat("s", "u") == ""
    assert seen == []


# ─── 各种响应怎么解析 ─────────────────────────────────────────
def test_returns_content(scripted_post):
    scripted_post([_ok("你好学生")])
    assert make_client().chat("s", "u") == "你好学生"


def test_4xx_does_not_retry(scripted_post):
    """4xx 是"我们请求得不对"(key 错/缺头/模型名错),重试一百次也一样。

    脚本只给 1 个响应,所以只要发生第二次请求就会炸 —— 这正是我们要钉住的行为。
    """
    seen = scripted_post([Resp(400, text="MissingSessionID")])
    assert make_client().chat("s", "u") == ""
    assert len(seen) == 1


def test_429_is_retried(scripted_post):
    """429 是限流,值得等一等再试 —— 应当试满 max_retries 次。"""
    seen = scripted_post([Resp(429, text="rate limited")] * 3)
    assert make_client(max_retries=3).chat("s", "u") == ""
    assert len(seen) == 3


def test_retries_then_succeeds(scripted_post):
    """先失败后成功:应当把最后一次的成功结果返回。"""
    seen = scripted_post([Resp(500, text="boom"), _ok("第二次成功")])
    assert make_client().chat("s", "u") == "第二次成功"
    assert len(seen) == 2


def test_network_exception_is_retried(scripted_post):
    """连不上也算失败,应当重试而不是直接抛出去。"""
    seen = scripted_post([requests.ConnectionError("断网"), _ok("恢复了")])
    assert make_client().chat("s", "u") == "恢复了"
    assert len(seen) == 2


def test_reasoning_content_fallback(scripted_post):
    """推理型模型 content 为空、只有 reasoning_content —— 退回取思维链,别返回空串。"""
    scripted_post([_ok("思维链内容", key="reasoning_content")])
    assert make_client().chat("s", "u") == "思维链内容"


# ─── _extract_text 的兼容分支(纯函数,不碰网络)─────────────
def test_extract_text_legacy_reply_field():
    assert _extract_text({"reply": "老结构答复"}) == "老结构答复"


def test_extract_text_prefers_content_over_reasoning():
    data = {"choices": [{"message": {"content": "正式答复",
                                     "reasoning_content": "思维链"}}]}
    assert _extract_text(data) == "正式答复"


def test_extract_text_handles_garbage():
    """结构不对不能抛异常 —— 它被包在 try 里就是为了这个。"""
    assert _extract_text({}) == ""
    assert _extract_text({"choices": []}) == ""
    assert _extract_text({"choices": [{}]}) == ""
