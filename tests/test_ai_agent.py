"""P1 AI Agent 内核测试：注册表 / 内置工具 / ReAct 循环 / SSE 路由

LLM 全部用脚本化 Fake Provider（不进 CI 真实调用）。
"""

import json
from datetime import date

import pytest

from backend.llm.base import BaseLLMProvider, LLMResponse, ToolCall


# ═══════════════════════════════════════════════════════════════════
# 1. 注册表
# ═══════════════════════════════════════════════════════════════════

class TestRegistry:
    def test_specs_shape(self):
        import backend.ai_tools.data_tools  # noqa: F401  确保内置工具已注册
        from backend.ai_tools.registry import tool_specs
        specs = tool_specs()
        assert len(specs) >= 13
        for s in specs:
            assert s["type"] == "function"
            fn = s["function"]
            assert fn["name"] and fn["description"]
            assert fn["parameters"]["type"] == "object"

    def test_allowed_filter(self):
        import backend.ai_tools.data_tools  # noqa: F401
        from backend.ai_tools.registry import tool_specs
        specs = tool_specs(allowed=["list_fund_pool"])
        assert [s["function"]["name"] for s in specs] == ["list_fund_pool"]

    @pytest.mark.asyncio
    async def test_execute_unknown_and_bad_args_envelope(self, db_session):
        from backend.ai_tools.registry import execute_tool
        bad = json.loads(await execute_tool(db_session, "no_such_tool", {}))
        assert bad["ok"] is False
        args_err = json.loads(await execute_tool(db_session, "get_signal_history", {"nope": 1}))
        assert args_err["ok"] is False and "参数" in args_err["error"]

    @pytest.mark.asyncio
    async def test_row_truncation(self, db_session, monkeypatch):
        from backend.ai_tools import registry as reg

        async def big(db):
            return [{"i": n} for n in range(600)]

        monkeypatch.setitem(reg._REGISTRY, "_test_big", reg.ToolDef(
            name="_test_big", description="", parameters={}, handler=big))
        out = json.loads(await reg.execute_tool(db_session, "_test_big", {}))
        assert out["ok"] and out["truncated"] is True
        assert len(out["data"]) == reg.MAX_TOOL_ROWS


# ═══════════════════════════════════════════════════════════════════
# 2. 内置数据工具（纯 DB 路径）
# ═══════════════════════════════════════════════════════════════════

async def _seed(db):
    from backend.models.analysis_result import AnalysisResult
    from backend.models.factor import Factor
    from backend.models.fund import Fund

    f1 = Fund(code="004011", name="基金A", fund_type="otc", status="active", tags="混合型-偏股")
    f2 = Fund(code="162719", name="基金B", fund_type="otc", status="inactive")
    db.add_all([f1, f2])
    await db.flush()
    scores = json.dumps({"momentum_6m": {"name": "动量", "raw_value": 0.12, "score": 1.0, "direction": "positive"}})
    db.add_all([
        AnalysisResult(fund_id=f1.id, analysis_date=date.today(), weighted_score=2.5,
                       signal_direction="buy", signal_strength="moderate_buy",
                       operation_advice="加仓", equity_ratio=0.7, factor_scores=scores,
                       original_score=2.0, dynamic_buy_threshold=1.5,
                       dynamic_sell_threshold=-1.5, quality_warnings='["风格漂移"]'),
        AnalysisResult(fund_id=f1.id, analysis_date=date(2026, 9, 20), weighted_score=-0.5,
                       signal_direction="hold", signal_strength="hold",
                       operation_advice="持有", equity_ratio=0.5, factor_scores=scores),
    ])
    db.add(Factor(code="momentum_6m", name="动量", weight=1.5, direction="positive",
                  normalization="cross_sectional_zscore", status="active"))
    await db.commit()
    return f1, f2


class TestDataTools:
    @pytest.mark.asyncio
    async def test_list_fund_pool_active_only(self, db_session):
        from backend.ai_tools.data_tools import t_list_fund_pool
        await _seed(db_session)
        rows = await t_list_fund_pool(db_session)
        assert [r["code"] for r in rows] == ["004011"]

    @pytest.mark.asyncio
    async def test_latest_signals_carries_diag_fields(self, db_session):
        from backend.ai_tools.data_tools import t_get_latest_signals
        await _seed(db_session)
        rows = await t_get_latest_signals(db_session)
        assert rows[0]["score"] == 2.5
        assert rows[0]["quality_warnings"] == ["风格漂移"]
        assert rows[0]["dynamic_sell_threshold"] == -1.5

    @pytest.mark.asyncio
    async def test_factor_history_and_snapshot(self, db_session):
        from backend.ai_tools.data_tools import t_get_factor_history, t_get_factor_snapshot
        await _seed(db_session)
        hist = await t_get_factor_history(db_session, code="004011", factor="momentum_6m", days=120)
        assert len(hist) == 2 and hist[-1]["raw_value"] == 0.12
        snap = await t_get_factor_snapshot(db_session)
        assert snap["004011"] == {"momentum_6m": 1.0}

    @pytest.mark.asyncio
    async def test_analysis_stats_flip_detection(self, db_session):
        from backend.ai_tools.data_tools import t_get_analysis_stats
        from backend.models.analysis_result import AnalysisResult
        f1, _ = await _seed(db_session)
        # sell → hold → buy：翻转只看相邻非 hold 信号，共 1 次
        db_session.add(AnalysisResult(
            fund_id=f1.id, analysis_date=date(2026, 9, 10), weighted_score=-3.0,
            signal_direction="sell", signal_strength="moderate_sell",
            operation_advice="减仓", equity_ratio=0.3, factor_scores="{}"))
        await db_session.commit()
        stats = await t_get_analysis_stats(db_session, days=30)
        assert stats["signal_distribution"] == {"buy": 1, "hold": 1, "sell": 1}
        assert stats["top_whipsaw"] == [{"code": "004011", "flips": 1, "observations": 3}]
        assert stats["warning_frequency"] == {"风格漂移": 1}

    @pytest.mark.asyncio
    async def test_missing_code_raises(self, db_session):
        from backend.ai_tools.data_tools import t_get_signal_history
        with pytest.raises(ValueError):
            await t_get_signal_history(db_session, code="999999")


# ═══════════════════════════════════════════════════════════════════
# 3. Agent 运行器（脚本化 Provider）
# ═══════════════════════════════════════════════════════════════════

class _ScriptedProvider(BaseLLMProvider):
    """按脚本轮次产出事件；messages 快照记录供断言回填正确性"""

    def __init__(self, rounds: list):
        super().__init__("fake-model", "k", "http://x")
        self.rounds = rounds
        self.calls: list[dict] = []

    async def chat(self, system_prompt, messages, max_tokens=2048, temperature=0.7) -> str:
        return "unused"

    async def astream_with_tools(self, messages, tools=None, max_tokens=4096, temperature=0.3):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        script = self.rounds[len(self.calls) - 1] if len(self.calls) <= len(self.rounds) else self.rounds[-1]
        for ev in script:
            yield ev


def _tool_round(name, args, tc_id="call_1"):
    resp = LLMResponse(content="", tool_calls=[ToolCall(id=tc_id, name=name, arguments=args)],
                       prompt_tokens=100, completion_tokens=20)
    return [{"type": "tool_call", "name": name, "arguments": args}, {"type": "final", "response": resp}]


def _text_round(text):
    resp = LLMResponse(content=text, tool_calls=[], prompt_tokens=120, completion_tokens=30)
    return [{"type": "delta", "text": text[:5]}, {"type": "delta", "text": text[5:]},
            {"type": "final", "response": resp}]


@pytest.fixture
def _seed_config(db_session):
    from backend.models.system_config import SystemConfig
    db_session.add_all([
        SystemConfig(config_key="ai_enabled", config_value="true"),
        SystemConfig(config_key="ai_api_key", config_value="test-key"),
        SystemConfig(config_key="ai_model", config_value="deepseek"),
    ])

    db_session.add_all([])
    return db_session


class TestAgentRunner:
    async def _run(self, db_session, prompt="查一下基金A的信号"):
        from backend.ai.agent_runner import AgentRunner
        import backend.ai_tools.data_tools  # noqa: F401  触发工具注册
        events = []
        async for ev in AgentRunner(db_session).run_stream(prompt):
            events.append(ev)
        return events

    @pytest.mark.asyncio
    async def test_tool_loop_then_final_and_persist(self, _seed_config):
        db = _seed_config
        await _seed(db)
        import backend.llm.factory as factory
        provider = _ScriptedProvider([
            _tool_round("get_latest_signals", {}),
            _text_round("最新信号：004011 评分 2.5，买入。"),
        ])
        from unittest.mock import patch
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            events = await self._run(db)
        types = [e["type"] for e in events]
        assert "start" in types and "round" in types
        assert "tool_call" in types and "tool_result" in types
        assert "delta_reset" in types  # 工具轮作废文本
        assert types[-1] == "final"
        tool_result = next(e for e in events if e["type"] == "tool_result")
        assert tool_result["tool"] == "get_latest_signals" and tool_result["ok"] is True
        # 第二轮 messages 含 role=tool 回填且格式合法
        second = provider.calls[1]["messages"]
        tool_msgs = [m for m in second if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert json.loads(tool_msgs[0]["content"])["ok"] is True
        assistant_tc = [m for m in second if m.get("role") == "assistant" and m.get("tool_calls")]
        assert assistant_tc and assistant_tc[0]["tool_calls"][0]["function"]["name"] == "get_latest_signals"
        # 落库：user + assistant(agent_events 轨迹)
        from backend.models.ai_conversation import AIConversation
        from sqlalchemy import select
        rows = (await db.execute(select(AIConversation).order_by(AIConversation.id))).scalars().all()
        assert [r.role for r in rows] == ["user", "assistant"]
        trace = json.loads(rows[1].agent_events)
        assert trace[0]["tool"] == "get_latest_signals"
        assert rows[1].context_type == "agent"

    @pytest.mark.asyncio
    async def test_max_rounds_forced_summary(self, _seed_config):
        db = _seed_config
        await _seed(db)
        import backend.llm.factory as factory
        # 前两轮永远发 tool_call 耗尽 max_rounds，收尾轮（无工具）给最终文本
        provider = _ScriptedProvider([
            _tool_round("list_fund_pool", {}, tc_id="c1"),
            _tool_round("list_fund_pool", {}, tc_id="c2"),
            _text_round("已基于两轮数据给出结论。"),
        ])
        from unittest.mock import patch
        from backend.ai.agent_runner import AgentRunner
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            runner = AgentRunner(db, max_rounds=2)
            events = [ev async for ev in runner.run_stream("loop")]
        finals = [e for e in events if e["type"] == "final"]
        assert finals and finals[0]["content"] == "已基于两轮数据给出结论。"
        # 收尾轮不带工具
        assert provider.calls[-1]["tools"] is None

    @pytest.mark.asyncio
    async def test_disabled_flag_errors(self, _seed_config):
        db = _seed_config
        from backend.models.system_config import SystemConfig
        from sqlalchemy import update
        await db.execute(update(SystemConfig).where(SystemConfig.config_key == "ai_api_key").values(config_value=""))
        await db.commit()
        import backend.llm.factory as factory
        from unittest.mock import patch
        from backend.ai.agent_runner import AgentRunner
        with patch.object(factory.LLMFactory, "create") as create:
            events = [ev async for ev in AgentRunner(db).run_stream("hi")]
        create.assert_not_called()
        assert events[0]["type"] == "error" and "Key" in events[0]["message"]

    @pytest.mark.asyncio
    async def test_llm_exception_maps_to_error_event(self, _seed_config):
        db = _seed_config

        class _Boom(_ScriptedProvider):
            async def astream_with_tools(self, messages, tools=None, max_tokens=4096, temperature=0.3):
                raise RuntimeError("tools is not supported by this model")
                yield  # pragma: no cover

        import backend.llm.factory as factory
        from unittest.mock import patch
        from backend.ai.agent_runner import AgentRunner
        with patch.object(factory.LLMFactory, "create", return_value=_Boom([])):
            events = [ev async for ev in AgentRunner(db).run_stream("hi")]
        err = next(e for e in events if e["type"] == "error")
        assert "不支持函数调用" in err["message"]

    @pytest.mark.asyncio
    async def test_model_name_400_maps_to_actionable_hint(self, _seed_config):
        """端点因模型名报 400 时要给出可操作提示，且不能把可用模型名清单截掉"""
        db = _seed_config
        boom = (
            "Error code: 400 - {'error': {'message': 'The supported API model names are "
            "deepseek-flash, deepseek-v4-pro, but you passed DeepSeek-V4.1-Flash. "
            "(request id: 20260924215315123456789012345678)'"
        )

        class _BadModel(_ScriptedProvider):
            async def astream_with_tools(self, messages, tools=None, max_tokens=4096, temperature=0.3):
                raise RuntimeError(boom)
                yield  # pragma: no cover

        import backend.llm.factory as factory
        from unittest.mock import patch
        from backend.ai.agent_runner import AgentRunner
        with patch.object(factory.LLMFactory, "create", return_value=_BadModel([])):
            events = [ev async for ev in AgentRunner(db).run_stream("hi")]
        err = next(e for e in events if e["type"] == "error")
        assert "deepseek-v4-pro" in err["message"]   # 端点列出的可用名必须完整可见
        # 旧 [:150] 正好把消息切在 "(reque" —— 与用户实报的残句一致
        assert "(request id" in err["message"]
        assert "模型 ID 覆盖" in err["message"]        # 指到该改的字段
        # 提示语必须完整：曾写成 msg += "A" \n "B"，第二句成裸语句被静默丢弃
        assert "对应 deepseek-flash）" in err["message"]

    def test_deepseek_preset_model_id_is_current_api_name(self):
        """DeepSeek 预设模型名必须是端点当前接受的 API 名（旧 deepseek-chat 已失效）"""
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create("deepseek", "k", "https://api.deepseek.com/v1")
        assert p.model_name == "deepseek-flash"
        # 显式 model_id 覆盖优先
        assert LLMFactory.create(
            "deepseek", "k", "https://api.deepseek.com/v1", model_id="deepseek-v4-pro"
        ).model_name == "deepseek-v4-pro"


# ═══════════════════════════════════════════════════════════════════
# 4. 路由：/tools 清单 与 /run SSE 封装
# ═══════════════════════════════════════════════════════════════════

class TestAgentRouter:
    @pytest.mark.asyncio
    async def test_list_tools_endpoint(self):
        from backend.routers.ai_agent import list_tools
        data = await list_tools()
        assert data["ok"] is True
        names = {t["name"] for t in data["tools"]}
        assert "get_latest_signals" in names and all(t["description"] for t in data["tools"])

    @pytest.mark.asyncio
    async def test_run_sse_data_frames(self, _seed_config):
        db = _seed_config
        await _seed(db)
        import backend.llm.factory as factory
        from unittest.mock import patch
        from backend.routers.ai_agent import AgentRunRequest, run_agent
        provider = _ScriptedProvider([_text_round("你好，一切正常。")])
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            resp = await run_agent(AgentRunRequest(prompt="hi"), db)
            frames = [chunk async for chunk in resp.body_iterator]
        assert resp.media_type == "text/event-stream"
        payloads = [json.loads(f[len("data: "):].strip()) for f in frames]
        assert payloads[0]["type"] == "start"
        assert payloads[-1]["type"] == "done"
        assert any(p["type"] == "final" for p in payloads)

    @pytest.mark.asyncio
    async def test_unknown_preset_task_400(self, _seed_config):
        from fastapi import HTTPException
        from backend.routers.ai_agent import AgentRunRequest, run_agent
        with pytest.raises(HTTPException) as ei:
            await run_agent(AgentRunRequest(prompt="x", task="nope"), _seed_config)
        assert ei.value.status_code == 400
