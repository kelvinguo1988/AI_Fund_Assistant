"""WorkBuddy Provider（workbuddy / workbuddy-ai）

与 zen-free 的本质区别：用**用户自己账号的 OAuth token**，消耗该账号套餐额度。
这些用例只验证请求构造与鉴权流程，不打真实上游。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm.workbuddy_provider import (  # noqa: E402
    SITES, UA_VERSION, WorkBuddyProvider,
)


class TestSites:
    def test_two_builds_with_distinct_apis(self):
        assert SITES["workbuddy"]["api"] == "https://copilot.tencent.com/v2"
        assert SITES["workbuddy-ai"]["api"] == "https://www.workbuddy.ai/v2"
        assert SITES["workbuddy-ai"]["ai"] is True
        assert SITES["workbuddy"]["ai"] is False

    def test_default_site_is_international(self):
        p = WorkBuddyProvider(api_key="rt")
        assert p.site_id == "workbuddy-ai"
        assert p.base_url == "https://www.workbuddy.ai/v2"


class TestHeaders:
    """实测缺这些头就被判「不是官方客户端」（11128）"""

    @pytest.mark.asyncio
    async def test_client_identity_headers(self):
        p = WorkBuddyProvider(api_key="rt")
        p._access_token = "at"          # 跳过换 token
        p._access_expires = 10**12
        h = await p._headers()
        assert h["User-Agent"] == f"WorkBuddy/{UA_VERSION}"  # 缺则 10085
        assert h["X-Product"] == "SaaS"
        assert h["X-IDE-Type"] == "WorkBuddy"
        assert h["X-Requested-With"] == "XMLHttpRequest"
        assert h["X-Agent-Intent"] == "craft"
        assert h["X-Agent-Purpose"] == "conversation"
        assert h["X-IDE-Name"] == "WorkBuddy"
        assert h["X-Agent-Type"] == "main", "workbuddy-ai 需要 X-Agent-Type"

    @pytest.mark.asyncio
    async def test_conversation_ids_present(self):
        """缺会话 id 头即 11128 通道校验失败——实测踩过"""
        p = WorkBuddyProvider(api_key="rt")
        p._access_token = "at"
        p._access_expires = 10**12
        h = await p._headers()
        for k in ("X-Conversation-ID", "X-Conversation-Message-ID",
                  "X-Conversation-Request-ID", "X-Request-ID"):
            assert h.get(k), f"{k} 必须存在且非空"
        assert h["X-Conversation-Message-ID"] == h["X-Request-ID"], "两者应是同一个 id"

    @pytest.mark.asyncio
    async def test_china_build_omits_agent_type(self):
        p = WorkBuddyProvider(api_key="rt", site_id="workbuddy")
        p._access_token = "at"
        p._access_expires = 10**12
        h = await p._headers()
        assert "X-Agent-Type" not in h


class TestAuth:
    @pytest.mark.asyncio
    async def test_missing_refresh_token_is_clear(self):
        p = WorkBuddyProvider(api_key="")
        with pytest.raises(RuntimeError, match="refresh token"):
            await p._ensure_access()

    @pytest.mark.asyncio
    async def test_valid_access_token_is_reused(self):
        p = WorkBuddyProvider(api_key="rt")
        p._access_token = "cached"
        p._access_expires = 10**12
        assert await p._ensure_access() == "cached", "未过期不该重复换 token"


class TestFactory:
    def test_both_presets_registered(self):
        from backend.llm.factory import LLMFactory

        for name in ("workbuddy", "workbuddy-ai"):
            p = LLMFactory.create(name, "rt", "")
            assert isinstance(p, WorkBuddyProvider)
            assert p.site_id == name
            assert p.model_name == "deepseek-v4.1-flash"

    def test_model_id_override(self):
        from backend.llm.factory import LLMFactory

        p = LLMFactory.create("workbuddy-ai", "rt", "", model_id="glm-5.3-flash")
        assert p.model_name == "glm-5.3-flash"


class TestModelFallback:
    """模型繁忙（14003/429）时自动换模型（2026-10-20）

    实测 deepseek-v4.1-flash 经常返回 14003「模型繁忙」，
    换 glm-5.3-flash 就正常；这类错误不该让整个请求失败。
    """

    def test_busy_detection(self):
        from backend.llm.workbuddy_provider import _is_model_busy

        assert _is_model_busy(429, "") is True
        assert _is_model_busy(400, '{"code":14003,"msg":"too many requests"}') is True
        assert _is_model_busy(400, '{"code": 14003}') is True

    def test_non_busy_is_not_retried(self):
        """模型不存在等错误不能当繁忙处理，否则会掩盖真实问题"""
        from backend.llm.workbuddy_provider import _is_model_busy

        assert _is_model_busy(400, '{"code":11102,"msg":"service info not found"}') is False
        assert _is_model_busy(401, '{"code":12153}') is False

    def test_candidates_include_primary_first(self):
        p = WorkBuddyProvider(api_key="rt", model_id="x") if False else WorkBuddyProvider(api_key="rt")
        p.model_name = "hy3"
        cands = p._model_candidates()
        assert cands[0] == "hy3", "首选必须排第一"
        assert "glm-5.3-flash" in cands, "繁忙时要能换到实测稳定的模型"
        assert len(cands) == len(set(cands)), "候选不能重复"

    @pytest.mark.asyncio
    async def test_busy_falls_back_to_next_model(self, monkeypatch):
        """14003 → 换下一个模型并成功"""
        p = WorkBuddyProvider(api_key="rt")
        p.model_name = "busy-model"
        p._access_token = "at"
        p._access_expires = 10**12
        tried: list[str] = []

        async def fake_once(model, messages, tools, max_tokens, temperature):
            tried.append(model)
            if model == "busy-model":
                from backend.llm.workbuddy_provider import _WorkBuddyHTTPError
                raise _WorkBuddyHTTPError(400, '{"code":14003,"msg":"too many requests"}')
            yield "OK", None, None

        monkeypatch.setattr(p, "_stream_once", fake_once)
        out = [x async for x in p._stream_chat([{"role": "user", "content": "hi"}], None, 10, 0.3)]
        assert out[0][0] == "OK"
        assert tried[0] == "busy-model" and len(tried) == 2, "应先试首选再降级"

    @pytest.mark.asyncio
    async def test_all_busy_raises_clearly(self, monkeypatch):
        p = WorkBuddyProvider(api_key="rt")
        p._access_token = "at"
        p._access_expires = 10**12

        async def fake_once(model, messages, tools, max_tokens, temperature):
            from backend.llm.workbuddy_provider import _WorkBuddyHTTPError
            raise _WorkBuddyHTTPError(429, "too many requests")
            yield  # pragma: no cover

        monkeypatch.setattr(p, "_stream_once", fake_once)
        with pytest.raises(RuntimeError, match="所有候选模型都繁忙"):
            [x async for x in p._stream_chat([{"role": "user", "content": "hi"}], None, 10, 0.3)]

    @pytest.mark.asyncio
    async def test_no_retry_after_content_emitted(self, monkeypatch):
        """已经流出去的内容收不回来——不要再换模型重发"""
        p = WorkBuddyProvider(api_key="rt")
        p._access_token = "at"
        p._access_expires = 10**12
        calls = []

        async def fake_once(model, messages, tools, max_tokens, temperature):
            from backend.llm.workbuddy_provider import _WorkBuddyHTTPError
            calls.append(model)
            yield "部分", None, None
            raise _WorkBuddyHTTPError(429, "too many requests")

        monkeypatch.setattr(p, "_stream_once", fake_once)
        got = []
        with pytest.raises(RuntimeError):
            async for item in p._stream_chat([{"role": "user", "content": "hi"}], None, 10, 0.3):
                got.append(item)
        assert got and got[0][0] == "部分"
        assert len(calls) == 1, "已产出内容后不得重试"
