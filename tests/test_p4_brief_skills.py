"""P4 闭环测试：Skill-as-tool 动态注册 + 每日简报（presets/build_brief_payload/调度分支）"""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.llm.base import BaseLLMProvider, LLMResponse, ToolCall
from backend.models.ai_skill import AISkill
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.push_channel import PushChannel
from backend.models.schedule import Schedule
from backend.models.system_config import SystemConfig


@pytest.fixture
def _seed_config(db_session):
    db_session.add_all([
        SystemConfig(config_key="ai_enabled", config_value="true"),
        SystemConfig(config_key="ai_api_key", config_value="test-key"),
        SystemConfig(config_key="ai_model", config_value="deepseek"),
    ])
    return db_session


class ScriptedProvider(BaseLLMProvider):
    def __init__(self, rounds):
        super().__init__("fake-model", "k", "http://x")
        self.rounds = rounds
        self.calls = []

    async def chat(self, system_prompt, messages, max_tokens=2048, temperature=0.7):
        return "unused"

    async def astream_with_tools(self, messages, tools=None, max_tokens=4096, temperature=0.3):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        script = self.rounds[min(len(self.calls) - 1, len(self.rounds) - 1)]
        for ev in script:
            yield ev


def _tool_round(name, args, tc_id="call_1"):
    resp = LLMResponse(content="", tool_calls=[ToolCall(id=tc_id, name=name, arguments=args)],
                       prompt_tokens=80, completion_tokens=15)
    return [{"type": "tool_call", "name": name, "arguments": args},
            {"type": "final", "response": resp}]


def _text_round(text):
    resp = LLMResponse(content=text, tool_calls=[], prompt_tokens=90, completion_tokens=20)
    return [{"type": "delta", "text": text}, {"type": "final", "response": resp}]


# ═══════════════════════════════════════════════════════════════════
# 1. tool_spec 解析与 Skill 工具加载
# ═══════════════════════════════════════════════════════════════════

class TestSkillToolLoading:
    def test_parse_tool_spec(self):
        from backend.ai_tools.skill_tools import parse_tool_spec
        assert parse_tool_spec(None) is None
        assert parse_tool_spec("  ") is None
        assert parse_tool_spec("not-json") is None
        assert parse_tool_spec('{"parameters": {}}') is None          # 缺 description
        spec = parse_tool_spec('{"description": "d", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}')
        assert spec["description"] == "d"
        assert "q" in spec["parameters"]["properties"]
        # parameters 非法 → 回退空 object schema
        spec2 = parse_tool_spec('{"description": "d"}')
        assert spec2["parameters"] == {"type": "object", "properties": {}}

    @pytest.mark.asyncio
    async def test_load_filters_and_names(self, db_session):
        db_session.add_all([
            AISkill(name="好技能", system_prompt="p", enabled=True,
                    tool_spec='{"description": "查询时获取本技能指导"}'),
            AISkill(name="坏spec", system_prompt="p", enabled=True, tool_spec="{oops"),
            AISkill(name="无spec", system_prompt="p", enabled=True, tool_spec=None),
            AISkill(name="停用", system_prompt="p", enabled=False,
                    tool_spec='{"description": "x"}'),
        ])
        await db_session.commit()
        from backend.ai_tools.skill_tools import load_skill_tools
        defs = await load_skill_tools(db_session)
        assert len(defs) == 1
        d = defs[0]
        assert d.name.startswith("skill_") and "[Skill: 好技能]" in d.description
        assert d.category == "skill"

    @pytest.mark.asyncio
    async def test_handler_renders_placeholders(self, db_session):
        f = Fund(code="004011", name="半导体A", fund_type="混合型", status="active")
        db_session.add(f)
        sk = AISkill(name="赛道指导", enabled=True,
                     system_prompt="关注基金池：{{fund_pool}}",
                     tool_spec='{"description": "赛道分析指导"}')
        db_session.add(sk)
        await db_session.commit()
        from backend.ai_tools.skill_tools import load_skill_tools
        defs = await load_skill_tools(db_session)
        out = await defs[0].handler(db_session)
        assert out["skill"] == "赛道指导"
        assert "004011" in out["guidance"] and "{{fund_pool}}" not in out["guidance"]


# ═══════════════════════════════════════════════════════════════════
# 2. registry extra/overrides + Agent 端到端调用 skill 工具
# ═══════════════════════════════════════════════════════════════════

class TestRegistryExtra:
    def test_tool_specs_merge_and_override(self):
        from backend.ai_tools.registry import ToolDef, tool_specs

        def mk(name, desc):
            async def h(db, **kw):
                return {}
            return ToolDef(name=name, description=desc, parameters={"type": "object", "properties": {}},
                           handler=h)

        names = [s["function"]["name"] for s in tool_specs(extra=[mk("alpha", "extra版")])]
        assert "alpha" in names
        # allowed 过滤不影响 extra 工具
        names2 = [s["function"]["name"] for s in tool_specs(allowed=["nothing"], extra=[mk("beta", "b")])]
        assert names2 == ["beta"]

    @pytest.mark.asyncio
    async def test_execute_tool_overrides(self, db_session):
        from backend.ai_tools.registry import ToolDef, execute_tool

        async def h(db, **kw):
            return {"hit": True}
        d = ToolDef(name="mine", description="x", parameters={"type": "object", "properties": {}}, handler=h)
        payload = json.loads(await execute_tool(db_session, "mine", {}, overrides={"mine": d}))
        assert payload["ok"] is True and payload["data"]["hit"] is True
        bad = json.loads(await execute_tool(db_session, "mine", {}))
        assert bad["ok"] is False and "未知工具" in bad["error"]

    @pytest.mark.asyncio
    async def test_agent_calls_skill_tool(self, _seed_config, db_session):
        db = _seed_config
        sk = AISkill(name="简报风格", enabled=True, system_prompt="输出保持简洁",
                     tool_spec='{"description": "获取输出风格指导"}')
        db.add(sk)
        await db.commit()
        skill_name = f"skill_{sk.id}"

        import backend.llm.factory as factory
        from unittest.mock import patch
        provider = ScriptedProvider([
            _tool_round(skill_name, {}),
            _text_round("已按风格指导回答。"),
        ])
        from backend.ai.agent_runner import AgentRunner
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            events = [ev async for ev in AgentRunner(db, max_rounds=3).run_stream("问题")]
        tr = next(e for e in events if e["type"] == "tool_result")
        assert tr["tool"] == skill_name and tr["ok"] is True
        # skill 工具注入 specs（即便不在内置注册表）
        assert any(s["function"]["name"] == skill_name for s in provider.calls[0]["tools"])
        # 回填 role=tool 含指导文本
        tool_msgs = [m for m in provider.calls[1]["messages"] if m.get("role") == "tool"]
        assert "输出保持简洁" in tool_msgs[0]["content"]
        assert events[-1]["type"] == "final" and "风格指导" in events[-1]["content"]


# ═══════════════════════════════════════════════════════════════════
# 3. 每日简报：payload / preset 注册 / run_collect
# ═══════════════════════════════════════════════════════════════════

async def _seed_brief_data(db):
    f1 = Fund(code="004011", name="半导体A", fund_type="混合型", status="active")
    f2 = Fund(code="011452", name="消费B", fund_type="股票型", status="active")
    db.add_all([f1, f2])
    await db.flush()
    today = date.today() - timedelta(days=1)
    db.add_all([
        AnalysisResult(fund_id=f1.id, analysis_date=today, weighted_score=3.2,
                       signal_direction="buy", signal_strength="强烈",
                       operation_advice="", factor_scores="{}"),
        AnalysisResult(fund_id=f2.id, analysis_date=today, weighted_score=-2.1,
                       signal_direction="sell", signal_strength="一般",
                       operation_advice="", factor_scores="{}"),
    ])
    await db.commit()
    return db


class TestDailyBrief:
    def test_preset_registered(self):
        from backend.ai_tools.registry import _REGISTRY
        from backend.routers.ai_agent import _PRESET_TASKS
        import backend.ai_tools.data_tools  # noqa: F401
        preset = _PRESET_TASKS["daily_brief"]
        assert preset["context_builder"] and preset["max_rounds"] == 3
        assert set(preset["allowed_tools"]) <= set(_REGISTRY)

    @pytest.mark.asyncio
    async def test_build_brief_payload(self, db_session):
        await _seed_brief_data(db_session)
        from types import SimpleNamespace
        from backend.services import market_regime_service as mrs

        async def _snap(self):
            return SimpleNamespace(regime="震荡", valuation_percentile=0.42,
                                   adv_decline_ratio=1.1, margin_change_pct_7d=0.02)
        from unittest.mock import patch
        with patch.object(mrs.MarketRegimeService, "get_snapshot", _snap):
            from backend.ai.presets import build_brief_payload
            payload = await build_brief_payload(db_session)
        sec = payload["sections"]
        assert sec["signal_overview"]["distribution"] == {"buy": 1, "sell": 1}
        assert sec["signal_overview"]["top_buy"][0]["code"] == "004011"
        assert sec["signal_overview"]["top_sell"][0]["code"] == "011452"
        assert sec["market_regime"]["regime"] == "震荡"
        # 调仓引擎在等权近似模式下可运行（数据少 → 清单可为空但结构在）
        assert "counts" in sec["rebalance"]
        assert isinstance(payload["caveats"], list)

    @pytest.mark.asyncio
    async def test_run_collect_returns_final(self, _seed_config, db_session):
        db = _seed_config
        await _seed_brief_data(db)
        from types import SimpleNamespace
        from backend.services import market_regime_service as mrs

        async def _snap(self):
            return SimpleNamespace(regime="震荡", valuation_percentile=None,
                                   adv_decline_ratio=None, margin_change_pct_7d=None)
        from unittest.mock import patch
        import backend.llm.factory as factory
        provider = ScriptedProvider([_text_round("# 今日简报\n基调观望。")])
        with patch.object(mrs.MarketRegimeService, "get_snapshot", _snap), \
                patch.object(factory.LLMFactory, "create", return_value=provider):
            from backend.ai.presets import run_collect
            text = await run_collect(db, task="daily_brief")
        assert text == "# 今日简报\n基调观望。"
        # 引擎上下文注入了 system prompt（信号分布可序列化）
        system_msg = provider.calls[0]["messages"][0]["content"]
        assert "signal_overview" in system_msg

    @pytest.mark.asyncio
    async def test_run_collect_unknown_task_and_error(self, db_session):
        from backend.ai.presets import run_collect
        with pytest.raises(ValueError):
            await run_collect(db_session, task="nope")
        # AI 未配置 → runner 发 error 事件 → RuntimeError
        with pytest.raises(RuntimeError):
            await run_collect(db_session, task="daily_brief")


# ═══════════════════════════════════════════════════════════════════
# 4. 调度分支：ai_daily_brief 不跑全量分析、失败静默
# ═══════════════════════════════════════════════════════════════════

class TestSchedulerBrief:
    @pytest.mark.asyncio
    async def test_brief_pushes_and_swallows_errors(self, db_session):
        from unittest.mock import patch
        from backend.scheduler.task_scheduler import TaskScheduler

        ch = PushChannel(name="飞书", channel_type="feishu",
                         webhook_url="https://open.feishu.cn/hook/x", token=None, enabled=True)
        db_session.add(ch)
        await db_session.commit()
        sched = Schedule(name="简报", task_type="ai_daily_brief", time_point="15:30",
                         channel_id=ch.id, enabled=True)
        db_session.add(sched)
        await db_session.commit()

        ts = TaskScheduler()
        sent = {}

        async def _send(self, content, title=None):
            sent["content"] = content
            return True

        import backend.ai.presets as presets
        with patch.object(presets, "run_collect", return_value="# 简报正文"), \
                patch("backend.push.feishu.FeishuPush.send", _send):
            await ts._run_ai_daily_brief(db_session, sched)
        assert sent["content"] == "# 简报正文"

        # 生成失败 → 静默不上抛
        boom = RuntimeError("LLM down")

        async def _fail(*a, **k):
            raise boom
        with patch.object(presets, "run_collect", _fail):
            await ts._run_ai_daily_brief(db_session, sched)  # 不抛异常

        # 无渠道 → 跳过推送
        sent.clear()
        sched2 = Schedule(name="简报2", task_type="ai_daily_brief", channel_id=None, enabled=True)
        db_session.add(sched2)
        await db_session.commit()
        with patch.object(presets, "run_collect", return_value="x"), \
                patch("backend.push.feishu.FeishuPush.send", _send):
            await ts._run_ai_daily_brief(db_session, sched2)
        assert "content" not in sent

    @pytest.mark.asyncio
    async def test_execute_task_once_branch_skips_analysis(self, db_session):
        """ai_daily_brief 调度只跑简报，不触发全量分析（防连打行情源）"""
        from unittest.mock import patch
        from backend.scheduler import task_scheduler as ts_mod
        from backend.scheduler.task_scheduler import TaskScheduler

        sched = Schedule(name="简报", task_type="ai_daily_brief", time_point="15:30", enabled=True)
        db_session.add(sched)
        await db_session.commit()

        calls = {"brief": 0, "analysis": 0}

        async def _trading_day(session, d):
            return True

        async def _brief(self, session, s):
            calls["brief"] += 1

        class _NoAnalysis:
            def __init__(self, *a, **k):
                pass

            async def run_analysis(self, *a, **k):
                calls["analysis"] += 1
                return []

        ts = TaskScheduler()
        import backend.services.analysis_service as an_svc
        import backend.data_sources.trading_calendar as cal
        with patch.object(cal, "is_a_share_trading_day_async", _trading_day), \
                patch.object(ts_mod, "async_session_factory", lambda: _session_cm(db_session)), \
                patch.object(an_svc, "AnalysisService", _NoAnalysis), \
                patch.object(TaskScheduler, "_run_ai_daily_brief", _brief):
            await ts._execute_task_once(sched.id)
        assert calls == {"brief": 1, "analysis": 0}

        # last_run_at 已更新
        await db_session.refresh(sched)
        assert sched.last_run_at is not None


class _session_cm:
    """把测试共享的 db_session 包装成 async_session_factory() 上下文"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


# ═══════════════════════════════════════════════════════════════════
# 5. 注册表自举（调度器不经 ai_agent 路由的惰性 import）
# ═══════════════════════════════════════════════════════════════════

class TestRegistryBootstrap:
    def test_package_import_populates_registry(self):
        """仅 import 包（不碰 HTTP 路由）也必须完成内置工具注册。

        曾因 data_tools 只在 ai_agent 路由函数内惰性 import，调度器生成的每日简报
        在空注册表下静默拿到 0 个工具。
        """
        import backend.ai_tools  # noqa: F401  仅包导入
        from backend.ai_tools.registry import _REGISTRY

        assert "list_fund_pool" in _REGISTRY
        assert len(_REGISTRY) >= 15

    def test_preset_task_tools_all_resolvable(self):
        """三个预置任务声明的 allowed_tools 必须真实存在且能注入。"""
        from backend.ai.presets import PRESET_TASKS
        from backend.ai_tools.registry import tool_specs

        for task, cfg in PRESET_TASKS.items():
            allowed = cfg.get("allowed_tools") or []
            specs = tool_specs(allowed)
            names = {s["function"]["name"] for s in specs}
            assert names == set(allowed), f"{task} 工具缺失: {set(allowed) - names}"

    def test_daily_brief_gets_tools_without_http(self):
        """模拟调度器视角：未调用任何 HTTP 端点，简报任务仍能拿到 4 个工具。"""
        from backend.ai.presets import PRESET_TASKS
        from backend.ai_tools.registry import tool_specs

        cfg = PRESET_TASKS["daily_brief"]
        assert len(tool_specs(cfg["allowed_tools"])) == len(cfg["allowed_tools"])
