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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ai.agent_runner import _assistant_toolcall_msg  # noqa: E402
# 复用 test_ai_agent 里的 fixture 与脚本化 Provider
from tests.test_ai_agent import _seed_config  # noqa: E402,F401
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


class TestEmptyFinalContent:
    """推理模型只吐思考内容、正文为空时不能报「未产出最终回答」（2026-10-20）

    线上现象：工具跑完了，最后返回「Agent 未产出最终回答」。两种成因都要拦住：
      1. 收尾轮只给了 delta、final.content 为空（前端已显示文字，后端却没记下）；
      2. 主循环最后一轮正文为空（模型只输出 reasoning_content）。
    """

    @pytest.mark.asyncio
    async def test_tail_round_deltas_count_as_answer(self, _seed_config):
        """收尾轮 final.content 为空但流了 delta → 仍应产出 final 事件"""
        from unittest.mock import patch
        import backend.llm.factory as factory
        from tests.test_ai_agent import _ScriptedProvider, _tool_round, _seed

        db = _seed_config
        await _seed(db)
        # 第 1 轮调工具；第 2 轮（收尾轮）只给 delta，final 的 content 为空
        tail = [
            {"type": "delta", "text": "42 只基金"},
            {"type": "final", "response": LLMResponse(content="", prompt_tokens=5, completion_tokens=3)},
        ]
        provider = _ScriptedProvider([_tool_round("list_fund_pool", {}), tail])
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            events = [e async for e in _runner(db).run_stream("几只基金")]
        errors = [e for e in events if e["type"] == "error"]
        assert not errors, f"不该报错: {[e['message'] for e in errors]}"
        finals = [e for e in events if e["type"] == "final"]
        assert finals and "42" in finals[0]["content"], "收尾轮的 delta 必须计入最终回答"

    @pytest.mark.asyncio
    async def test_empty_main_round_retries_for_content(self, _seed_config):
        """主循环正文为空 → 再要一次正文，而不是直接放弃"""
        from unittest.mock import patch
        import backend.llm.factory as factory
        from tests.test_ai_agent import _ScriptedProvider, _text_round, _seed

        db = _seed_config
        await _seed(db)
        empty = [
            {"type": "final", "response": LLMResponse(
                content="", reasoning="我在想……", prompt_tokens=10, completion_tokens=5)},
        ]
        provider = _ScriptedProvider([empty, _text_round("42")])
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            events = [e async for e in _runner(db).run_stream("几只基金")]
        assert len(provider.calls) >= 2, "正文为空时应重试一次"
        finals = [e for e in events if e["type"] == "final"]
        assert finals and finals[0]["content"].strip() == "42"


def _runner(db):
    from backend.ai.agent_runner import AgentRunner
    import backend.ai_tools.data_tools  # noqa: F401  触发工具注册
    return AgentRunner(db)
