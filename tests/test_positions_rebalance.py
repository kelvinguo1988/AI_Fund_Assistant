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
        assert rows == [{"code": "004011", "shares": 100.0, "cost_nav": None,
                         "first_buy_date": None}]
        assert errors == []

    def test_headerless_and_bad_rows(self):
        from backend.services.position_service import parse_positions_csv
        rows, errors = parse_positions_csv("004011,1000.5,1.234\nabc,5,6\n004011,0,1")
        assert rows == [{"code": "004011", "shares": 1000.5, "cost_nav": 1.234,
                         "first_buy_date": None}]
        assert any("表头" in e for e in errors)
        assert any("abc" in e for e in errors)
        assert any("份额" in e for e in errors)

    def test_first_buy_date_column_and_dirty_values(self):
        """首买日列：认得「首次买入日期/买入日期/确认日期」，认不出来就留 None 而不是猜"""
        from backend.services.position_service import parse_positions_csv
        rows, errors = parse_positions_csv(
            "基金代码,持有份额,首次买入日期\n"
            "004011,1000,2026-08-01\n"
            "011452,500,2026/9/3\n"
            "630010,300,未知\n"
        )
        assert [r["first_buy_date"] for r in rows] == [
            date(2026, 8, 1), date(2026, 9, 3), None,
        ]
        assert errors == []

    def test_future_first_buy_date_reported_not_imported(self):
        """晚于今天的首买日会把持有期算成 0 天、凭空拦掉建议 → 按未填处理并说明"""
        from backend.services.position_service import parse_positions_csv
        rows, errors = parse_positions_csv("基金代码,持有份额,买入日期\n004011,1000,2099-01-01")
        assert rows[0]["first_buy_date"] is None
        assert any("晚于今天" in e for e in errors)

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

    @pytest.mark.asyncio
    async def test_first_buy_date_survives_dateless_reimport(self, db_session):
        """再导入不带日期列的 CSV 不能抹掉手工补录的首买日（None = 不动，不是清空）"""
        from backend.services.position_service import list_positions, upsert_position
        await _seed_positions_db(db_session)
        await upsert_position(db_session, "004011", 1000, 2.0, first_buy_date=date(2026, 8, 1))
        await upsert_position(db_session, "004011", 900, 2.2)
        items = await list_positions(db_session)
        assert items[0]["first_buy_date"] == "2026-08-01"
        assert items[0]["shares"] == 900

    @pytest.mark.asyncio
    async def test_first_buy_date_in_the_future_rejected(self, db_session):
        from backend.services.position_service import upsert_position
        await _seed_positions_db(db_session)
        with pytest.raises(ValueError):
            await upsert_position(db_session, "004011", 100, 2.0, first_buy_date=date(2099, 1, 1))

    @pytest.mark.asyncio
    async def test_put_distinguishes_absent_from_cleared(self, db_session):
        """PUT 用「字段是否出现」决定改不改：不带该键 = 不动，显式 null = 清除"""
        from backend.routers.position import PositionUpdate, update_position
        from backend.services.position_service import upsert_position
        await _seed_positions_db(db_session)
        await upsert_position(db_session, "004011", 1000, 2.0, first_buy_date=date(2026, 8, 1))
        p = (await db_session.execute(select(UserPosition))).scalars().first()

        await update_position(p.id, PositionUpdate(shares=1234), db_session)
        assert p.first_buy_date == date(2026, 8, 1) and p.shares == 1234

        await update_position(p.id, PositionUpdate(first_buy_date=None), db_session)
        assert p.first_buy_date is None

    @pytest.mark.asyncio
    async def test_put_rejects_future_first_buy_date(self, db_session):
        """PUT 与 POST/CSV 同口径：未来首买日会把持有期算成 0 天、整张工单被拦掉"""
        from fastapi import HTTPException
        from backend.routers.position import PositionUpdate, update_position
        from backend.services.position_service import upsert_position
        await _seed_positions_db(db_session)
        await upsert_position(db_session, "004011", 1000, 2.0, first_buy_date=date(2026, 8, 1))
        p = (await db_session.execute(select(UserPosition))).scalars().first()
        with pytest.raises(HTTPException):
            await update_position(p.id, PositionUpdate(
                first_buy_date=date.today() + timedelta(days=30)), db_session)
        assert p.first_buy_date == date(2026, 8, 1)


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


# ═══════════════════════════════════════════════════════════════════
# 5. Q10：市值权重口径 + 持有期/阶梯赎回费
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def clean_nav_cache():
    """隔离实时估值缓存：权重口径只读它，测试之间不能互相串味"""
    import time as _t
    from backend.services.fund_realtime_service import FundRealtimeService
    saved = dict(FundRealtimeService._estimate_cache)
    FundRealtimeService._estimate_cache.clear()
    yield _t.time, FundRealtimeService
    FundRealtimeService._estimate_cache.clear()
    FundRealtimeService._estimate_cache.update(saved)


def _seed_position(db, fund, shares, cost_nav=None, first_buy_date=None):
    p = UserPosition(fund_id=fund.id, shares=shares, cost_nav=cost_nav,
                     first_buy_date=first_buy_date)
    db.add(p)
    return p


class TestFeeLadderPure:
    def test_parse_sorts_and_keeps_none_last(self):
        from backend.ai.holding_fee import parse_fee_ladder
        assert parse_fee_ladder([[365, 0.5], [7, 1.5], [None, 0.25]]) == [
            (7, 1.5), (365, 0.5), (None, 0.25)]

    def test_parse_drops_dirty_rows_and_falls_back(self):
        from backend.ai.holding_fee import DEFAULT_FEE_LADDER, parse_fee_ladder
        # 单行脏（上界不可解析 / 上界≤0 / 费率越界）只丢那一行
        assert parse_fee_ladder([[7, 1.5], ["x", 9], [0, 0.5], [7, 999]]) == [(7, 1.5)]
        # 全脏 / 类型错 → 回落默认阶梯：费率算成空等于"免赎回费"，不能默认成立
        assert parse_fee_ladder([[None, -1]]) == [(b, p) for b, p in DEFAULT_FEE_LADDER]
        assert parse_fee_ladder("乱码") == [(b, p) for b, p in DEFAULT_FEE_LADDER]

    def test_fee_pct_tiers_and_boundaries(self):
        from backend.ai.holding_fee import fee_pct_for_holding, parse_fee_ladder
        ladder = parse_fee_ladder(None)
        assert fee_pct_for_holding(0, ladder) == 1.5
        assert fee_pct_for_holding(3, ladder) == 1.5
        assert fee_pct_for_holding(7, ladder) == 0.5      # 上界不含：满 7 天出惩罚档
        assert fee_pct_for_holding(100, ladder) == 0.5
        assert fee_pct_for_holding(365, ladder) == 0.25
        assert fee_pct_for_holding(400, ladder) == 0.25
        assert fee_pct_for_holding(None, ladder) is None   # 未知 ≠ 0 天

    def test_days_to_next_tier(self):
        from backend.ai.holding_fee import days_to_next_fee_tier, parse_fee_ladder
        ladder = parse_fee_ladder(None)
        assert days_to_next_fee_tier(3, ladder) == 4
        assert days_to_next_fee_tier(100, ladder) == 265
        assert days_to_next_fee_tier(400, ladder) == 0     # 已在最低档
        assert days_to_next_fee_tier(None, ladder) == 0

    def test_ladder_text_and_holding_days(self):
        from backend.ai.holding_fee import holding_days_between, ladder_text, parse_fee_ladder
        assert ladder_text(parse_fee_ladder(None)) == "<7天 1.5% / <365天 0.5% / ≥365天 0.25%"
        assert holding_days_between(date(2026, 9, 1), date(2026, 9, 10)) == 9
        assert holding_days_between(date(2026, 9, 10), date(2026, 9, 1)) == 0
        assert holding_days_between(None, date(2026, 9, 10)) is None

    def test_parse_date_flex(self):
        from backend.ai.holding_fee import parse_date_flex
        assert parse_date_flex("2026-09-01") == date(2026, 9, 1)
        assert parse_date_flex("2026/9/1") == date(2026, 9, 1)
        assert parse_date_flex("2026年9月1日") == date(2026, 9, 1)
        assert parse_date_flex("20260901") == date(2026, 9, 1)
        assert parse_date_flex("2026-13-40") is None       # 不猜：越界日期算未填
        assert parse_date_flex("持有中") is None and parse_date_flex("") is None


class TestPositionWeights:
    @pytest.mark.asyncio
    async def test_no_cache_uses_cost_market_and_flags_sentinel_gone(self, db_session, clean_nav_cache):
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0)     # 成本市值 2000
        _seed_position(db_session, funds["011452"], 500, 4.0)      # 成本市值 2000
        await db_session.commit()
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        h = {x["code"]: x for x in rpt.holdings}
        # 旧哨兵口径下漏填成本的那只会被压成 ≈0.006%；这里必须是真实市值比
        assert h["004011"]["weight_pct"] == pytest.approx(50.0)
        assert h["011452"]["weight_pct"] == pytest.approx(50.0)
        assert {x["weight_basis"] for x in rpt.holdings} == {"cost"}
        gap = next(c for c in rpt.caveats if "组合权重口径" in c)
        assert "004011" in gap and "011452" in gap and "按成本市值" in gap

    @pytest.mark.asyncio
    async def test_cached_nav_market_wins_over_cost(self, db_session, clean_nav_cache):
        now, frs = clean_nav_cache
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0)     # 缓存净值 3.0 → 3000
        _seed_position(db_session, funds["011452"], 500, 4.0)      # 无缓存 → 成本 2000
        await db_session.commit()
        frs._estimate_cache["004011"] = (now(), {"code": "004011", "nav": 3.0})
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        h = {x["code"]: x for x in rpt.holdings}
        assert h["004011"]["weight_pct"] == pytest.approx(60.0)
        assert h["011452"]["weight_pct"] == pytest.approx(40.0)
        assert h["004011"]["weight_basis"] == "market" and h["011452"]["weight_basis"] == "cost"
        assert "市值（实时净值）1 只" in next(c for c in rpt.caveats if "组合权重口径" in c)

    @pytest.mark.asyncio
    async def test_stale_cache_entry_is_ignored(self, db_session, clean_nav_cache):
        now, frs = clean_nav_cache
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0)
        await db_session.commit()
        frs._estimate_cache["004011"] = (now() - 8 * 86400, {"code": "004011", "nav": 9.9})
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        assert rpt.holdings[0]["weight_basis"] == "cost"

    @pytest.mark.asyncio
    async def test_no_cost_falls_back_to_share_proportional(self, db_session, clean_nav_cache):
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000)
        _seed_position(db_session, funds["011452"], 500)
        await db_session.commit()
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        h = {x["code"]: x for x in rpt.holdings}
        # 份额比 2:1，但量纲是元：不会像 1.0 哨兵那样归一后 ≈0
        assert h["004011"]["weight_pct"] == pytest.approx(66.7)
        assert h["011452"]["weight_pct"] == pytest.approx(33.3)
        assert {x["weight_basis"] for x in rpt.holdings} == {"shares"}
        assert "既无净值也未填成本" in next(c for c in rpt.caveats if "组合权重口径" in c)


class TestFeeGateInReport:
    @pytest.mark.asyncio
    async def test_punitive_fee_downgrades_sell_to_watch(self, db_session, clean_nav_cache):
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0, date.today() - timedelta(days=3))
        await db_session.commit()
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        assert [x["code"] for x in rpt.sells] == []      # 该卖但不值得卖
        w = next(x for x in rpt.watch if x["code"] == "004011")
        assert w["confirm_days"] == 4                     # 再持有 4 天出 1.5% 档
        assert "持有 3 天" in w["reason"] and "赎回费 1.5%" in w["reason"]
        assert "另有恶化佐证" in w["reason"]              # 降级不隐瞒恶化事实
        assert w["holding_days"] == 3 and w["estimated_fee_pct"] == 1.5

    @pytest.mark.asyncio
    async def test_nonpunitive_fee_annotates_sell_and_swap(self, db_session, clean_nav_cache):
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0, date.today() - timedelta(days=100))
        await db_session.commit()
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        assert [x["code"] for x in rpt.sells] == ["004011"]
        assert rpt.sells[0]["reasons"][-1] == "持有 100 天，适用赎回费 0.5%"
        assert rpt.sells[0]["estimated_fee_pct"] == 0.5
        assert rpt.swaps[0]["sell_fee_pct"] == 0.5 and rpt.swaps[0]["sell_holding_days"] == 100
        assert "赎回费阶梯：<7天 1.5%" in rpt.summary_md()

    @pytest.mark.asyncio
    async def test_missing_first_buy_date_says_unknown_not_zero(self, db_session, clean_nav_cache):
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0)
        await db_session.commit()
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        # 默认 0 天 = 1.5% 惩罚档 = 所有建议被拦掉，裁定明确禁止
        assert [x["code"] for x in rpt.sells] == ["004011"]
        assert "持有期未知" in rpt.sells[0]["reasons"][-1]
        assert rpt.sells[0]["holding_days"] is None and rpt.sells[0]["estimated_fee_pct"] is None
        assert "未填首次买入日" in next(c for c in rpt.caveats if "持有期" in c)

    @pytest.mark.asyncio
    async def test_rollback_switch_removes_fee_from_worklist(self, db_session, clean_nav_cache):
        from backend.models.system_config import SystemConfig
        db_session.add(SystemConfig(config_key="quality_filter_config",
                                    config_value='{"redemption_fee_enabled": 0}'))
        funds = await _seed_rebalance_pool(db_session)
        _seed_position(db_session, funds["004011"], 1000, 2.0, date.today() - timedelta(days=3))
        await db_session.commit()
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        assert rpt.fee_policy["enabled"] is False
        assert [x["code"] for x in rpt.sells] == ["004011"]     # 回到旧工单
        assert not any("赎回费" in t for t in rpt.sells[0]["reasons"])
        assert rpt.sells[0]["holding_days"] is None
        assert "已关闭（redemption_fee_enabled=0）" in rpt.summary_md()

    @pytest.mark.asyncio
    async def test_pool_mode_has_no_holding_period_claims(self, db_session, clean_nav_cache):
        await _seed_rebalance_pool(db_session)
        from backend.ai.rebalance import RebalanceService
        rpt = await RebalanceService(db_session).analyze(window_days=30, otc_status_map={})
        assert rpt.sells and rpt.sells[0]["holding_days"] is None
        assert rpt.sells[0]["reasons"][1].startswith("窗口内因子分总和恶化")
        assert not any("未填首次买入日" in c for c in rpt.caveats)
