"""思考模式的 reasoning_content 必须随轮次回传（2026-10-20）

线上真实报错：
  RuntimeError: ZenFree HTTP 400 ... "The `reasoning_content` in the
  thinking mode must be passed back to the API."

原因：agent 多轮工具调用回填 assistant 消息时只带了 content 与 tool_calls，
把推理内容丢了。DeepSeek 系推理模型在第一轮有 tool_calls 时要求后续轮次
原样带回该轮的 reasoning_content。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ai.agent_runner import _assistant_toolcall_msg  # noqa: E402
from backend.llm.base import LLMResponse, ToolCall  # noqa: E402


class TestLLMResponseCarriesReasoning:
    def test_default_is_empty(self):
        """非推理模型不受影响"""
        assert LLMResponse().reasoning == ""

    def test_field_kept(self):
        r = LLMResponse(reasoning="先看池子再回答")
        assert r.reasoning == "先看池子再回答"


class TestAssistantMessagePassthrough:
    def test_reasoning_is_sent_back(self):
        """有 reasoning 时必须写进回传的 assistant 消息"""
        tcs = [ToolCall(id="c1", name="list_fund_pool", arguments={"status": "active"})]
        msg = _assistant_toolcall_msg("", tcs, reasoning="我需要先查基金池")
        assert msg["reasoning_content"] == "我需要先查基金池"
        assert msg["role"] == "assistant"
        assert msg["tool_calls"][0]["function"]["name"] == "list_fund_pool"

    def test_no_reasoning_keeps_payload_clean(self):
        """空推理不要塞个空串进去，避免非推理端点拒收未知字段"""
        tcs = [ToolCall(id="c1", name="list_fund_pool", arguments={})]
        msg = _assistant_toolcall_msg("正文", tcs)
        assert "reasoning_content" not in msg

    def test_empty_string_reasoning_omitted(self):
        tcs = [ToolCall(id="c1", name="x", arguments={})]
        msg = _assistant_toolcall_msg("", tcs, reasoning="")
        assert "reasoning_content" not in msg


class TestProviderForwardCompat:
    """回填用的字段名必须与上游要求的完全一致（下划线命名）"""

    def test_field_name_matches_upstream(self):
        tcs = [ToolCall(id="c1", name="x", arguments={})]
        msg = _assistant_toolcall_msg("", tcs, reasoning="r")
        assert "reasoning_content" in msg
        assert "reasoning" not in msg, "上游要的是 reasoning_content"
