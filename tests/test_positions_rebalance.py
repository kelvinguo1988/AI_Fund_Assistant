"""P3 调仓分析测试：持仓 CRUD/CSV 解析 + RebalanceService 四清单与组合约束 + Agent 挂接"""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.user_position import UserPosition


# ═══════════════════════════════════════════════════════════════════
# 1. CSV 解析（纯函数）
# ═══════════════════════════════════════════════════════════════════

class TestCsvParse:
    def test_alipay_style_header(self):
        from backend.services.position_service import parse_positions_csv
        text = (
            "基金代码,基金名称,持有份额,持仓成本价\n"
            "004011,半导体A,1200.5,1.2345\n"
            "011452,半导体B,\"2,000\",2.5\n"
            "合计,,,3200.5,\n"
        )
        rows, errors = parse_positions_csv(text)
        assert [(r["code"], r["shares"], r["cost_nav"]) for r in rows] == [
            ("004011", 1200.5, 1.2345), ("011452", 2000.0, 2.5),
        ]
        assert any("不是 6 位数字" in e for e in errors)

    def test_tiantian_style_aliases(self):
        from backend.services.position_service import parse_positions_csv
        rows, errors = parse_positions_csv("证券代码,份额,成本价,持仓占比\n004011,100,,45%")
        assert rows == [{"code": "004011", "shares": 100.0, "cost_nav": None}]
        assert errors == []

    def test_headerless_and_bad_rows(self):
        from backend.services.position_service import parse_positions_csv
        rows, errors = parse_positions_csv("004011,1000.5,1.234\nabc,5,6\n004011,0,1")
        assert rows == [{"code": "004011", "shares": 1000.5, "cost_nav": 1.234}]
        assert any("表头" in e for e in errors)
        assert any("abc" in e for e in errors)
        assert any("份额" in e for e in errors)

    def test_empty(self):
        from backend.services.position_service import parse_positions_csv
        rows, errors = parse_positions_csv("  ")
        assert rows == [] and errors


# ═══════════════════════════════════════════════════════════════════
# 2. 持仓 CRUD / 导入路由
# ═══════════════════════════════════════════════════════════════════

async def _seed_positions_db(db):
    f1 = Fund(code="004011", name="半导体A", fund_type="otc", status="active",
              exposure_tags="半导体设备材料×4 45.0%, 军工×1 6.0% (覆盖51%)")
    f2 = Fund(code="011452", name="半导体B", fund_type="otc", status="active",
              exposure_tags="半导体设备材料×4 45.0% (覆盖45%)")
    db.add_all([f1, f2])
    await db.flush()
    db.add(AnalysisResult(fund_id=f1.id, analysis_date=date.today(), weighted_score=2.5,
                          signal_direction="buy", signal_strength="moderate_buy",
                          operation_advice="", equity_ratio=0.7, factor_scores="{}"))
    await db.commit()
    return f1, f2


class TestPositionCrud:
    @pytest.mark.asyncio
    async def test_upsert_create_then_update(self, db_session):
        from backend.services.position_service import upsert_position, list_positions
        await _seed_positions_db(db_session)
        r = await upsert_position(db_session, "004011", 1000, 2.0)
        assert r["created"] is True
        r2 = await upsert_position(db_session, "004011", 800, None)
        assert r2["created"] is False
        items = await list_positions(db_session)
        assert len(items) == 1
        assert items[0]["shares"] == 800 and items[0]["cost_nav"] is None
        assert items[0]["latest_score"] == 2.5 and items[0]["fund_code"] == "004011"

    @pytest.mark.asyncio
    async def test_upsert_validation(self, db_session):
        from backend.services.position_service import upsert_position
        await _seed_positions_db(db_session)
        with pytest.raises(ValueError):
            await upsert_position(db_session, "999999", 100)
        with pytest.raises(ValueError):
            await upsert_position(db_session, "004011", 0)

    @pytest.mark.asyncio
    async def test_import_csv_merge_and_not_in_pool(self, db_session):
        from backend.routers.position import import_csv, PositionImportRequest
        await _seed_positions_db(db_session)
        resp = await import_csv(PositionImportRequest(text=(
            "基金代码,持有份额,持仓成本价\n"
            "004011,1000,2.5\n"
            "999999,500,1.2\n"
        ), mode="merge"), db_session)
        assert resp.data["imported"] == 1
        assert any("不在基金池" in e for e in resp.data["errors"])
        assert len((await db_session.execute(select(UserPosition))).scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_import_csv_replace_mode(self, db_session):
        from backend.routers.position import import_csv, PositionImportRequest
        from backend.services.position_service import upsert_position
        await _seed_positions_db(db_session)
        await upsert_position(db_session, "011452", 10, 1)
        resp = await import_csv(PositionImportRequest(
            text="004011,500,1.5", mode="replace"), db_session)
        assert resp.data["imported"] == 1
        rows = (await db_session.execute(select(UserPosition))).scalars().all()
        assert len(rows) == 1
        f1 = await db_session.get(Fund, rows[0].fund_id)
        assert f1.code == "004011" and rows[0].source == "import"


# ═══════════════════════════════════════════════════════════════════
# 3. 调仓引擎
# ═══════════════════════════════════════════════════════════════════

def _ar(fid, d, score, direction, factors=None, warnings=None,
        buy_th=None, sell_th=None):
    return AnalysisResult(
        fund_id=fid, analysis_date=d, weighted_score=score,
        signal_direction=direction, signal_strength="x",
        operation_advice="", equity_ratio=0.5,
        factor_scores=json.dumps(factors or {}),
        quality_warnings=json.dumps(warnings) if warnings else None,
        dynamic_buy_threshold=buy_th, dynamic_sell_threshold=sell_th,
    )


async def _seed_rebalance_pool(db):
    """A 卖出 / B 买入(同赛道) / C 买入被否决 / D 阈值边界 / E 翻转高发"""
    today = date.today()
    a = Fund(code="004011", name="卖出A", fund_type="otc", status="active",
             exposure_tags="半导体设备材料×4 45.0%, 军工×1 6.0% (覆盖51%)")
    b = Fund(code="011452", name="买入B", fund_type="otc", status="active",
             exposure_tags="半导体设备材料×4 45.0% (覆盖45%)")
    c = Fund(code="006751", name="暂停C", fund_type="otc", status="active")
    d = Fund(code="630010", name="边界D", fund_type="otc", status="active")
    e = Fund(code="022365", name="翻转E", fund_type="otc", status="active")
    db.add_all([a, b, c, d, e])
    await db.flush()
    f_up = {"f1": {"score": 1}, "f2": {"score": 1}}
    f_down = {"f1": {"score": -1}, "f2": {"score": -1}}
    db.add_all([
        _ar(a.id, today - timedelta(days=10), 1.0, "buy", f_up),
        _ar(a.id, today, -2.5, "sell", f_down, warnings=["风格漂移"], sell_th=-2.0),
        _ar(b.id, today, 3.0, "buy", buy_th=1.5),
        _ar(c.id, today, 2.2, "buy"),
        _ar(d.id, today, 1.2, "hold"),
        _ar(e.id, today - timedelta(days=12), 2.0, "buy"),
        _ar(e.id, today - timedelta(days=8), -2.0, "sell"),
        _ar(e.id, today - timedelta(days=4), 2.0, "buy"),
        _ar(e.id, today, 0.0, "hold"),
    ])
    # A×B 重仓重合 5 只（双胞胎警示）
    for fid in (a.id, b.id):
        for i in range(5):
            db.add(FundHolding(fund_id=fid, stock_code=f"60000{i}", stock_name=f"股{i}",
                               ratio=3.0, quarter_label="2026年2季度股票投资明细"))
    await db.commit()
    return {f.code: f for f in (a, b, c, d, e)}


def _codes(items):
    return [x["code"] for x in items]


class TestRebalanceEngine:
    @pytest.mark.asyncio
    async def test_pool_equal_weight_approx_mode(self, db_session):
        from backend.ai.rebalance import RebalanceService
        await _seed_rebalance_pool(db_session)
        rpt = await RebalanceService(db_session).analyze(
            window_days=30, otc_status_map={"006751": {"purchase": "暂停申购"}})
        assert rpt.holdings_mode == "pool_equal_weight_approx"
        assert _codes(rpt.sells) == ["004011"]
        assert "窗口内因子分总和恶化" in rpt.sells[0]["reasons"][1]
        assert "质量警告：风格漂移" in rpt.sells[0]["reasons"][2]
        assert _codes(rpt.buys) == ["011452"]           # C 被否决
        assert _codes(rpt.buy_blocked) == ["006751"]
        assert rpt.swaps[0]["sell_code"] == "004011" and rpt.swaps[0]["buy_code"] == "011452"
        assert rpt.swaps[0]["theme"] == "半导体设备材料"
        assert rpt.swaps[0]["score_gap"] == pytest.approx(5.5)
        assert set(_codes(rpt.watch)) == {"630010", "022365"}
        ew = next(w for w in rpt.watch if w["code"] == "022365")
        assert ew["confirm_days"] == 3 and "翻转 2 次" in ew["reason"]
        next(w for w in rpt.watch if w["code"] == "630010")["confirm_days"] == 2

    @pytest.mark.asyncio
    async def test_portfolio_constraints(self, db_session):
        from backend.ai.rebalance import RebalanceService
        await _seed_rebalance_pool(db_session)
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        c = rpt.constraints
        assert c["theme_concentration"][0]["theme"] == "半导体设备材料"
        assert c["theme_concentration"][0]["weight_pct"] == pytest.approx(40.0)
        twin = c["twin_pairs"][0]
        assert {twin["a_code"], twin["b_code"]} == {"004011", "011452"}
        assert twin["common"] == 5
        assert any("申购状态数据不可用" in v for v in rpt.caveats)

    @pytest.mark.asyncio
    async def test_positions_mode_weights_and_overlap(self, db_session):
        from backend.ai.rebalance import RebalanceService
        funds = await _seed_rebalance_pool(db_session)
        db_session.add(UserPosition(fund_id=funds["004011"].id, shares=1000, cost_nav=2.0))
        await db_session.commit()
        rpt = await RebalanceService(db_session).analyze(
            window_days=30, otc_status_map={"011452": {"purchase": "开放申购"}})
        assert rpt.holdings_mode == "positions"
        assert rpt.sells[0]["code"] == "004011" and rpt.sells[0]["weight_pct"] == 100.0
        b = next(x for x in rpt.buys if x["code"] == "011452")
        assert b["overlap_note"] == "与持仓重仓重合 5 只"
        assert b["otc_status"] == "开放申购"
        assert _codes(rpt.holdings) == ["004011"]

    @pytest.mark.asyncio
    async def test_no_analysis_rows(self, db_session):
        from backend.ai.rebalance import RebalanceService
        db_session.add(Fund(code="004011", name="A", fund_type="otc", status="active"))
        await db_session.commit()
        rpt = await RebalanceService(db_session).analyze(otc_status_map={})
        assert any("无分析记录" in v for v in rpt.caveats)

    @pytest.mark.asyncio
    async def test_summary_md_contains_lists(self, db_session):
        from backend.ai.rebalance import RebalanceService
        await _seed_rebalance_pool(db_session)
        rpt = await RebalanceService(db_session).analyze(
            window_days=30, otc_status_map={"006751": {"purchase": "封闭期"}})
        md = rpt.summary_md()
        assert "调仓工单" in md and "卖出候选" in md and "同赛道换仓配对" in md
        assert "买入B" in md and "买入被否决" in md


# ═══════════════════════════════════════════════════════════════════
# 4. Agent 挂接：get_positions 工具 + rebalance 预置任务
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def _seed_config(db_session):
    from backend.models.system_config import SystemConfig
    db_session.add_all([
        SystemConfig(config_key="ai_enabled", config_value="true"),
        SystemConfig(config_key="ai_api_key", config_value="test-key"),
        SystemConfig(config_key="ai_model", config_value="deepseek"),
    ])
    return db_session


class TestAgentWiring:
    @pytest.mark.asyncio
    async def test_get_positions_tool(self, db_session):
        from backend.ai_tools.data_tools import t_get_positions
        from backend.services.position_service import upsert_position
        await _seed_positions_db(db_session)
        assert await t_get_positions(db_session) == []
        await upsert_position(db_session, "004011", 100, 1.5)
        rows = await t_get_positions(db_session)
        assert rows[0]["code"] == "004011" and rows[0]["latest_score"] == 2.5

    def test_rebalance_preset_registered(self):
        from backend.routers.ai_agent import _PRESET_TASKS
        from backend.ai_tools.registry import _REGISTRY
        import backend.ai_tools.data_tools  # noqa: F401
        preset = _PRESET_TASKS["rebalance"]
        assert preset["context_builder"] and preset["max_rounds"] == 5
        assert set(preset["allowed_tools"]) <= set(_REGISTRY)

    @pytest.mark.asyncio
    async def test_run_rebalance_task_sse(self, _seed_config):
        import backend.llm.factory as factory
        from unittest.mock import patch
        from backend.routers.ai_agent import AgentRunRequest, run_agent
        from backend.llm.base import BaseLLMProvider, LLMResponse

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

        resp_text = LLMResponse(content="换仓结论。", tool_calls=[],
                                prompt_tokens=50, completion_tokens=10)
        provider = ScriptedProvider([[{"type": "delta", "text": "换仓结论。"},
                                      {"type": "final", "response": resp_text}]])
        db = _seed_config
        await _seed_rebalance_pool(db)
        with patch.object(factory.LLMFactory, "create", return_value=provider):
            resp = await run_agent(AgentRunRequest(task="rebalance"), db)
            frames = [c async for c in resp.body_iterator]
        payloads = [json.loads(f[len("data: "):].strip()) for f in frames]
        # 预置任务带引擎上下文：context 事件先于 runner 的 start
        assert payloads[0]["type"] == "context" and payloads[0]["note"]
        assert payloads[1]["type"] == "start"
        system_msg = provider.calls[0]["messages"][0]
        assert system_msg["role"] == "system"
        assert "sells" in system_msg["content"] and "004011" in system_msg["content"]
        assert payloads[-1]["type"] == "done"
