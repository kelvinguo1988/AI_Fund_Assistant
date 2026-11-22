"""基金评审团回归测试 — 评委规则/共识加权/落库闭环/endpoint（全部零网络）"""

import sys, os
from datetime import date
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.models.analysis_result import AnalysisResult
from backend.models.backtest_result import BacktestResult
from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.fund_quarterly import FundQuarterly
from backend.services.fund_panel_service import (
    FundPanelService, PanelContext, _consensus, judge_size_health,
    judge_temp_discipline, judge_graham_cross, judge_money_flow,
    fund_group_kind, GROUP_WEIGHTS,
)


def _ctx(**kw) -> PanelContext:
    """最小上下文（温度 None → G 派默认弃权，需要时逐字段覆盖）"""
    fund = Fund(code="016874", name="广发远见智选混合C", fund_type="otc",
                status="active")
    fund.id = 1
    return PanelContext(fund=fund, **kw)


# ── 单评委规则 ───────────────────────────────────────────────────────

class TestJudges:
    def test_temp_discipline_zones(self):
        hot = _ctx(temperature={"temperature": 75.0, "zone": "高估"})
        cold = _ctx(temperature={"temperature": 25.0, "zone": "低估"})
        mid = _ctx(temperature={"temperature": 44.2, "zone": "合理偏低"})
        none = _ctx(temperature=None)
        assert judge_temp_discipline(hot)["signal"] == "bearish"
        assert judge_temp_discipline(cold)["signal"] == "bullish"
        assert judge_temp_discipline(mid)["signal"] == "neutral"
        assert judge_temp_discipline(none)["signal"] == "skip"

    def test_graham_cross_quadrants(self):
        mk = lambda st, note="n": _ctx(temperature={
            "graham": {"available": True, "percentile_10y": 97.5,
                       "cross": {"state": st, "note": note}}})
        assert judge_graham_cross(mk("true_cheap"))["signal"] == "bullish"
        assert judge_graham_cross(mk("rate_distorted_cheap"))["signal"] == "neutral"
        assert judge_graham_cross(mk("true_expensive"))["signal"] == "bearish"
        assert judge_graham_cross(mk("rare_review"))["signal"] == "bearish"
        # note 不得重复"利率扭曲型伪低估"前缀（烟测发现的文案 bug 锁定）
        distorted = judge_graham_cross(
            mk("rate_distorted_cheap", "利率扭曲型伪低估——温度计显示便宜"))
        assert distorted["reasoning"].count("利率扭曲型伪低估") == 1
        # 块不可用 → 弃权
        off = _ctx(temperature={"graham": {"available": False}})
        assert judge_graham_cross(off)["signal"] == "skip"

    def test_size_health_unit_is_yuan(self):
        """fund_size 存储单位是元（468.68 亿 = 46868000000 元），阈值判定必须换算"""
        big = _ctx(fund_size=46868000000.0)   # 468.68 亿（创业板ETF 实测值）
        mini = _ctx(fund_size=1.5e8)          # 1.5 亿
        none = _ctx(fund_size=None)
        assert judge_size_health(big)["signal"] == "bullish"
        assert "468.68 亿" in judge_size_health(big)["reasoning"]
        assert judge_size_health(mini)["signal"] == "bearish"
        assert judge_size_health(none)["signal"] == "skip"

    def test_money_flow_skip_when_unavailable(self):
        assert judge_money_flow(_ctx())["signal"] == "skip"
        inflow = _ctx(inflow_available=True, inflow_weighted_pct=1.2,
                      inflow_basis="ETF 快照主力净占比（当日）")
        assert judge_money_flow(inflow)["signal"] == "bullish"
        assert "ETF 快照" in judge_money_flow(inflow)["reasoning"]
        outflow = _ctx(inflow_available=True, inflow_weighted_pct=-2.0)
        assert judge_money_flow(outflow)["signal"] == "bearish"


# ── 共识与类型分组 ───────────────────────────────────────────────────

class TestConsensus:
    def test_weighted_vote(self):
        sig = [
            {"group": "G", "signal": "bullish"},
            {"group": "A", "signal": "bullish"},
            {"group": "Q", "signal": "bearish"},
            {"group": "R", "signal": "skip"},
        ]
        c = _consensus(sig, GROUP_WEIGHTS["equity"])
        # G 0.30 + A 0.30 投多，Q 0.25 投空 → (0.30+0.30-0.25)/0.85 ≈ +0.41
        assert c["verdict"] == "加码倾向"
        assert c["bull"] == 2 and c["bear"] == 1 and c["abstain"] == 1
        assert c["score"] > 0.25

    def test_split_note_on_divergence(self):
        sig = [{"group": "G", "signal": "bullish"}, {"group": "A", "signal": "bullish"},
               {"group": "Q", "signal": "bearish"}, {"group": "R", "signal": "bearish"}]
        c = _consensus(sig, GROUP_WEIGHTS["default"])
        assert c["verdict"] == "持有观望"
        assert c["split_note"], "4+ 有效票且接近分裂时应提示分歧"

    def test_all_skip_yields_hold(self):
        sig = [{"group": "G", "signal": "skip"} for _ in range(3)]
        c = _consensus(sig, GROUP_WEIGHTS["default"])
        assert c["verdict"] == "持有观望" and c["score"] == 0.0

    def test_fund_group_kind(self):
        assert fund_group_kind("QDII-混合", "纳指ETF") == "overseas"
        assert fund_group_kind("债券型-混合二级", "某债C") == "bond"
        assert fund_group_kind("混合型-偏股", "某成长混合") == "equity"
        assert fund_group_kind(None, "某物联智能C") == "default"


# ── convene 集成（conftest 已屏蔽乐咕网络：温度/指数估值评委自动弃权）──

async def _seed_full_fund(db, code="016874", fund_id=101):
    f = Fund(id=fund_id, code=code, name="广发远见智选混合C", fund_type="otc",
             status="active", fund_type_official="混合型-偏股",
             benchmark_text="沪深300指数收益率*60%+人民币计价的恒生指数收益率*20%"
                            "+中债-新综合财富(总值)指数收益率*20%",
             tags="沪深300|恒生|偏股", exposure_tags='[{"tag":"半导体","ratio":0.55}]')
    db.add(f)
    db.add(AnalysisResult(
        fund_id=fund_id, analysis_date=date(2026, 11, 20), weighted_score=2.2,
        signal_direction="buy", signal_strength="买入", equity_ratio=0.6,
        factor_scores='{"inv_volatility": 0.8, "momentum": 1.2, "drawdown_recovery": 0.3}',
        dynamic_buy_threshold=1.5, dynamic_sell_threshold=3.0,
    ))
    db.add(BacktestResult(
        fund_id=fund_id, fund_code=code, fund_name="广发远见智选混合C",
        period=365, effectiveness_window=5,
        max_drawdown=28.5, excess_return=5.2, low_sample=False,
        signal_coverage_ratio=0.55, effectiveness_rate=72.0,
    ))
    db.add(FundQuarterly(fund_id=fund_id, report_date="2026-09-30",
                         effective_date="2026-10-01", fund_size=12.3e8))
    db.add(FundHolding(fund_id=fund_id, stock_code="600519", stock_name="贵州茅台",
                       ratio=9.5, quarter_label="2026Q3", report_date="2026-09-30"))
    await db.commit()
    return fund_id


@pytest.mark.asyncio
async def test_convene_full_pipeline(db_session, monkeypatch):
    """全链路：13 评委、弃权显式、共识结构、summary_md（温度/估值走 conftest 屏蔽）"""
    fid = await _seed_full_fund(db_session)
    panel = await FundPanelService.convene(db_session, fid)

    assert panel["fund_code"] == "016874"
    assert panel["fund_kind"] == "equity"
    assert len(panel["signals"]) == 13
    # 温度/指数估值评委在 conftest 屏蔽下应显式弃权而不是给假数据
    by_id = {s["id"]: s for s in panel["signals"]}
    assert by_id["temp_discipline"]["signal"] == "skip"
    assert by_id["index_valuation"]["signal"] == "skip"
    # 库内数据驱动的评委应正常工作
    assert by_id["size_health"]["signal"] == "bullish"
    assert "12.30 亿" in by_id["size_health"]["reasoning"]
    assert by_id["drawdown_discipline"]["signal"] == "bearish"
    assert by_id["excess_return"]["signal"] == "bullish"
    assert by_id["alpha_quality"]["signal"] == "bullish"
    assert by_id["exposure_focus"]["signal"] == "bearish"  # 半导体 55% ≥ 40%
    assert by_id["pool_crowding"]["signal"] == "bullish"   # 池内仅 1 只「半导体」→ 稀缺
    assert panel["consensus"]["verdict"] in ("加码倾向", "持有观望", "减配倾向")
    assert "评审团" in panel["summary_md"]


@pytest.mark.asyncio
async def test_convene_missing_fund(db_session):
    from backend.services.fund_panel_service import FundPanelService
    with pytest.raises(ValueError):
        await FundPanelService.convene(db_session, 99999)


@pytest.mark.asyncio
async def test_log_consensus_into_advice_log(db_session, monkeypatch):
    """共识落 advice_log(source='panel') → stats.by_source 可对比两套引擎"""
    from backend.services.advice_learning_service import AdviceLearningStore

    fid = await _seed_full_fund(db_session, code="024738", fund_id=102)
    panel = await FundPanelService.convene(db_session, fid)
    c = panel["consensus"]
    # 强共识才落库；这里不假设方向，直接构造 consensus dict 测 log 映射
    for verdict, action in (("加码倾向", "buy"), ("减配倾向", "sell")):
        aid = AdviceLearningStore().log_advice(
            "024738", action, f"[评审团] {verdict}", score=50.0, source="panel")
        assert aid is not None
        # 同日同方向去重
        assert AdviceLearningStore().log_advice(
            "024738", action, f"[评审团] {verdict}", source="panel") is None

    # stats.evaluated 是 JOIN outcomes 的已判定样本：给两条 panel 建议回填 outcomes
    # （评审团样本与调仓样本同流程：落库 → 满 30 净值日回填 → 进统计）
    store = AdviceLearningStore()
    rows = store._conn.execute(
        "SELECT id, action FROM advice_log WHERE source='panel'").fetchall()
    for aid, action in rows:
        store.record_outcome(aid, action, fund_change=4.0, bench_change=2.0,
                             eval_date="2026-11-20")
    st = store.stats()
    assert "by_source" in st
    assert st["by_source"]["panel"]["evaluated"] == 2
    assert st["by_source"]["panel"]["buy_total"] == 1
    assert st["by_source"]["panel"]["sell_total"] == 1
    # 存量/调仓来源行与评审团行互不混淆
    reb = st["by_source"].get("rebalance", {"evaluated": 0})
    assert st["by_source"]["panel"]["evaluated"] + reb["evaluated"] == st["evaluated"]


# ── endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_panel_endpoint(db_session):
    from backend.routers.analysis import fund_panel

    fid = await _seed_full_fund(db_session, code="008715", fund_id=103)
    resp = await fund_panel(fund_id=fid, log=False, db=db_session)
    data = resp.data
    assert data["fund_code"] == "008715"
    assert len(data["signals"]) == 13
    assert data.get("log_result") is None  # log=False 不落库


@pytest.mark.asyncio
async def test_panel_endpoint_missing(db_session):
    from backend.routers.analysis import fund_panel
    import fastapi
    with pytest.raises(fastapi.HTTPException):
        await fund_panel(fund_id=88888, log=False, db=db_session)


# ── skill 注册 ───────────────────────────────────────────────────────

def test_panel_skill_registered():
    from backend.ai_tools.skill_tools import _DATA_SKILLS
    assert _DATA_SKILLS.get("基金评审团") == "_run_fund_panel"


@pytest.mark.asyncio
async def test_panel_skill_engine(db_session):
    """数据型 skill 引擎：缺 fund_id 报错提示；带 fund_id 返回 panel"""
    from backend.ai_tools.skill_tools import _run_fund_panel

    err = await _run_fund_panel(db_session)
    assert "fund_id" in err["error"]

    fid = await _seed_full_fund(db_session, code="018896", fund_id=104)
    out = await _run_fund_panel(db_session, fund_id=str(fid))
    assert out["panel"]["fund_code"] == "018896"
    assert out.get("logged") is None  # 默认不落库
