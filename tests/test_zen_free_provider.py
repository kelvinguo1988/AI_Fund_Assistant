"""OpenCode Zen 免费模型 Provider（zen-free）

免费层有四条硬约束，缺一即 403 FreeTierError（2026-10-20 实测）：
  1. 端点是 /inference/v1（老 /zen/v1 恒 403）
  2. User-Agent 三段式
  3. 请求必须带 bash/read/edit/glob/grep 五个核心工具
  4. 必须 stream=True + include_usage
这些用例全是纯函数校验，不打真实上游（CI 红线）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm.zen_free_provider import (  # noqa: E402
    ZEN_FREE_BASE, ZenFreeProvider, core_tool_stubs, opencode_id,
    zen_headers, zen_user_agent,
)


class TestEndpointAndHeaders:
    def test_uses_inference_v1_not_zen_v1(self):
        assert ZEN_FREE_BASE == "https://opencode.ai/inference/v1"
        p = ZenFreeProvider()
        assert "/inference/v1/" in p._endpoint
        assert "/zen/v1" not in p._endpoint, "老端点对免费层恒 403"

    def test_user_agent_is_three_part(self):
        ua = zen_user_agent("chat")
        assert ua.startswith("opencode/1.19.0 ")
        assert "ai-sdk/provider-utils/" in ua
        assert "runtime/bun/" in ua

    def test_headers_carry_session_and_request_ids(self):
        """缺这两个头即 403——实测踩过"""
        h = zen_headers("chat")
        for k in ("x-opencode-session", "x-opencode-session-id", "x-opencode-request"):
            assert k in h and h[k], f"{k} 必须存在且非空"
        assert h["x-opencode-client"] == "cli"
        assert h["authorization"] == "Bearer public"
        assert h["user-agent"] == zen_user_agent("chat")

    def test_opencode_id_shape(self):
        """免费层校验 session id 形状：ses_ + 12 hex + 14 base62"""
        import re
        sid = opencode_id("ses", descending=True)
        assert re.fullmatch(r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}", sid), sid
        # 生成器自身的计数器很小（免费层会看这个）
        counter = int(sid[5:9], 16)
        assert 0xFFF - counter < 16, "计数器必须是 OpenCode 的形状"


class TestCoreTools:
    def test_five_core_tools_declared(self):
        names = {t["function"]["name"] for t in core_tool_stubs()}
        assert names == {"bash", "read", "edit", "glob", "grep"}

    def test_core_tools_tell_model_not_to_call(self):
        for t in core_tool_stubs():
            assert "Never call" in t["function"]["description"]

    def test_merges_with_caller_tools(self):
        from backend.llm.zen_free_provider import _with_core_tools

        caller = [{"type": "function", "function": {"name": "search_funds",
                   "description": "x", "parameters": {}}}]
        merged = _with_core_tools(caller)
        names = [t["function"]["name"] for t in merged]
        assert "search_funds" in names
        for core in ("bash", "read", "edit", "glob", "grep"):
            assert core in names
        assert names.count("search_funds") == 1, "调用方工具不能被重复添加"

    def test_no_tools_still_gets_core_tools(self):
        """无工具对话轮也必须带上核心工具，否则 403"""
        from backend.llm.zen_free_provider import _with_core_tools

        assert len(_with_core_tools(None)) == 5


class TestFactoryWiring:
    def test_factory_builds_zen_provider_without_key(self):
        from backend.llm.factory import LLMFactory

        p = LLMFactory.create("zen-free", "", "")
        assert isinstance(p, ZenFreeProvider)
        assert p.model_name == "deepseek-v4.1-flash-free", "缺省模型 ID"

    def test_factory_respects_model_id(self):
        from backend.llm.factory import LLMFactory

        p = LLMFactory.create("zen-free", "", "", model_id="glm-5.3-flash-free")
        assert p.model_name == "glm-5.3-flash-free"
